"""
Binance Futures User Data Stream — ORDER_TRADE_UPDATE + ALGO_UPDATE listener.

Runs in a daemon thread. On each fill event it dispatches to LiveExecutor.
Market order fills (entry, emergency close) arrive via ORDER_TRADE_UPDATE;
bracket (SL/TP) fills arrive via the separate ALGO_UPDATE event since Binance
moved conditional orders to the Algo Order API. On disconnect it obtains a
new listenKey, reconnects, and runs a catch-up query to find any fills that
arrived during the outage.
"""

from __future__ import annotations

import json
import threading
import time
from typing import TYPE_CHECKING

from app.db.connection import get_conn
from app.utils.logger import get_logger

if TYPE_CHECKING:
    from app.live.exchange import BinanceExchangeClient
    from app.live.executor import LiveExecutor

logger = get_logger(__name__)

_KEEPALIVE_INTERVAL = 20 * 60   # PUT listenKey every 20 minutes


class UserDataStream:
    """
    WebSocket listener for Binance Futures ORDER_TRADE_UPDATE events.

    Parameters
    ----------
    client   : BinanceExchangeClient
    executor : LiveExecutor
    lock     : threading.Lock — shared with executor; acquired before every dispatch
    """

    def __init__(
        self,
        client: BinanceExchangeClient,
        executor: LiveExecutor,
        lock: threading.Lock,
    ) -> None:
        self._client   = client
        self._executor = executor
        self._lock     = lock
        self._listen_key: str = ""
        self._stop_flag       = threading.Event()
        self._thread: threading.Thread | None  = None
        self._ka_thread: threading.Thread | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._stop_flag.clear()
        self._listen_key = self._client.get_listen_key()
        self._thread = threading.Thread(
            target=self._run,
            name="user-data-stream",
            daemon=True,
        )
        self._thread.start()
        self._ka_thread = threading.Thread(
            target=self._keepalive_loop,
            name="uds-keepalive",
            daemon=True,
        )
        self._ka_thread.start()
        logger.info("[UDS] Started  listenKey=%s…", self._listen_key[:12])

    def stop(self) -> None:
        self._stop_flag.set()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _run(self) -> None:
        while not self._stop_flag.is_set():
            try:
                self._connect_and_listen()
            except Exception as exc:
                if self._stop_flag.is_set():
                    break
                logger.warning("[UDS] Connection error: %s — reconnecting in 5s", exc)
                time.sleep(5)
                self._on_disconnect()

    def _connect_and_listen(self) -> None:
        import websocket  # websocket-client

        url = f"{self._client.ws_base}/ws/{self._listen_key}"
        logger.info("[UDS] Connecting to %s…", url)

        def on_message(ws, raw):
            try:
                self._on_message(json.loads(raw))
            except Exception as exc:
                logger.error("[UDS] on_message error: %s", exc)

        def on_error(ws, error):
            logger.warning("[UDS] WebSocket error: %s", error)

        def on_close(ws, code, reason):
            logger.warning("[UDS] Connection closed: %s %s", code, reason)

        ws = websocket.WebSocketApp(
            url,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )
        ws.run_forever(ping_interval=0)

        if not self._stop_flag.is_set():
            raise ConnectionError("WebSocket disconnected")

    def _keepalive_loop(self) -> None:
        while not self._stop_flag.wait(timeout=_KEEPALIVE_INTERVAL):
            if self._listen_key:
                self._client.keepalive_listen_key(self._listen_key)
                logger.debug("[UDS] listenKey keepalive sent")

    def _on_disconnect(self) -> None:
        """Get a fresh listenKey and run catch-up before reconnecting."""
        try:
            self._listen_key = self._client.get_listen_key()
            logger.info("[UDS] New listenKey obtained — running catch-up")
            self._catch_up()
        except Exception as exc:
            logger.error("[UDS] _on_disconnect setup failed: %s", exc)

    def _catch_up(self) -> None:
        """
        For every open live_trade, check open orders on exchange.
        If either bracket order is missing, query order history to find fill
        and dispatch the appropriate handler.
        """
        with self._lock:
            with get_conn() as conn:
                rows = [
                    dict(r) for r in conn.execute(
                        "SELECT * FROM live_trades WHERE status='open'"
                    ).fetchall()
                ]

            for trade in rows:
                symbol = trade["symbol"]
                sl_id  = str(trade.get("exchange_sl_id") or "")
                tp_id  = str(trade.get("exchange_tp_id") or "")

                try:
                    open_orders = self._client.get_open_algo_orders(symbol)
                except Exception as exc:
                    logger.warning("[UDS] catch-up: open_orders %s failed: %s", symbol, exc)
                    continue

                open_ids  = {str(o["algoId"]) for o in open_orders}
                sl_exists = sl_id in open_ids
                tp_exists = tp_id in open_ids

                if sl_exists and tp_exists:
                    continue  # both resting — nothing missed

                # Check each potentially filled order
                for order_id, kind in [(tp_id, "tp"), (sl_id, "sl")]:
                    if not order_id or order_id in open_ids:
                        continue
                    try:
                        resp = self._client.get_algo_order(symbol, order_id)
                        if resp.get("algoStatus") != "FINISHED":
                            continue
                        fill  = float(resp.get("actualPrice") or resp.get("triggerPrice") or 0)
                        qty   = float(resp.get("actualQty") or 0)
                        tid   = trade["id"]
                        partial_taken = int(trade.get("partial_taken") or 0)

                        if kind == "tp" and partial_taken == 0:
                            logger.info("[UDS] catch-up: %s partial TP filled @ %.4f", symbol, fill)
                            self._executor.handle_partial_tp(tid, fill, qty)
                        elif kind == "sl":
                            logger.info("[UDS] catch-up: %s SL hit @ %.4f", symbol, fill)
                            self._executor.handle_sl_hit(tid, fill)
                        elif kind == "tp" and partial_taken == 1:
                            logger.info("[UDS] catch-up: %s final TP hit @ %.4f", symbol, fill)
                            self._executor.handle_tp_hit(tid, fill)
                    except Exception as exc:
                        logger.warning("[UDS] catch-up order query %s failed: %s", order_id, exc)

    # ── Event dispatch ────────────────────────────────────────────────────────

    def _on_message(self, msg: dict) -> None:
        event = msg.get("e")
        if event == "ORDER_TRADE_UPDATE":
            self._on_order_trade_update(msg)
        elif event == "ALGO_UPDATE":
            self._on_algo_update(msg)

    def _on_order_trade_update(self, msg: dict) -> None:
        order   = msg.get("o", {})
        symbol  = order.get("s")
        status  = order.get("X")          # order status
        order_id = str(order.get("i", ""))  # orderId
        order_type = order.get("ot", "")  # STOP_MARKET, TAKE_PROFIT_MARKET, etc.

        if status != "FILLED":
            return

        fill_price = float(order.get("ap") or order.get("sp") or 0)  # avgPrice or stopPrice
        fill_qty   = float(order.get("z") or 0)   # cumulative filled qty

        logger.info(
            "[UDS] ORDER_TRADE_UPDATE %s  orderId=%s  type=%s  fill=%.4f  qty=%.6f",
            symbol, order_id, order_type, fill_price, fill_qty,
        )

        self._dispatch_fill(symbol, order_id, fill_price, fill_qty)

    def _on_algo_update(self, msg: dict) -> None:
        """
        Handle bracket (SL/TP) order status changes. Algo/conditional order
        fills are NOT reported via ORDER_TRADE_UPDATE — Binance pushes them
        on this separate event since the Algo Order API migration.
        """
        algo   = msg.get("o", {})
        symbol = algo.get("s")
        algo_status = algo.get("X")
        algo_id     = str(algo.get("aid", ""))

        if algo_status in ("CANCELED", "EXPIRED"):
            return

        if algo_status == "REJECTED":
            logger.warning(
                "[UDS] ALGO_UPDATE %s  algoId=%s  REJECTED  reason=%s",
                symbol, algo_id, algo.get("rm"),
            )
            return

        if algo_status != "FINISHED":
            return  # NEW / TRIGGERING / TRIGGERED — not filled yet

        fill_price = float(algo.get("ap") or 0)
        fill_qty   = float(algo.get("aq") or 0)
        if not fill_price or not fill_qty:
            logger.debug(
                "[UDS] ALGO_UPDATE %s  algoId=%s  FINISHED with no fill — ignoring",
                symbol, algo_id,
            )
            return

        logger.info(
            "[UDS] ALGO_UPDATE %s  algoId=%s  type=%s  fill=%.4f  qty=%.6f",
            symbol, algo_id, algo.get("o", ""), fill_price, fill_qty,
        )

        self._dispatch_fill(symbol, algo_id, fill_price, fill_qty)

    def _dispatch_fill(self, symbol: str, filled_id: str, fill_price: float, fill_qty: float) -> None:
        """Route a filled order/algo id to the matching live_trades row's handler."""
        with self._lock:
            with get_conn() as conn:
                rows = [
                    dict(r) for r in conn.execute(
                        "SELECT * FROM live_trades WHERE symbol=? AND status='open'",
                        (symbol,),
                    ).fetchall()
                ]

            for trade in rows:
                sl_id         = str(trade.get("exchange_sl_id") or "")
                tp_id         = str(trade.get("exchange_tp_id") or "")
                partial_taken = int(trade.get("partial_taken") or 0)
                tid           = trade["id"]

                if filled_id == tp_id and partial_taken == 0:
                    logger.info("[UDS] Routing to handle_partial_tp id=%d", tid)
                    self._executor.handle_partial_tp(tid, fill_price, fill_qty)

                elif filled_id == sl_id and partial_taken == 0:
                    logger.info("[UDS] Routing to handle_sl_hit (full, before partial) id=%d", tid)
                    self._executor.handle_sl_hit(tid, fill_price)

                elif filled_id == sl_id and partial_taken == 1:
                    logger.info("[UDS] Routing to handle_sl_hit (remainder, after partial) id=%d", tid)
                    self._executor.handle_sl_hit(tid, fill_price)

                elif filled_id == tp_id and partial_taken == 1:
                    logger.info("[UDS] Routing to handle_tp_hit (final) id=%d", tid)
                    self._executor.handle_tp_hit(tid, fill_price)
