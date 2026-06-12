"""
PriceMonitor — background thread for intrabar SL/TP and limit-fill detection.

Primary transport: Binance USDT-M futures WebSocket (miniTicker stream),
giving ~1s price updates instead of 60s REST polling. Falls back to REST
polling automatically if the WebSocket library is missing or the stream
errors, and retries the stream after a cool-off.

Monitors every symbol that has an open trade OR a pending limit order.

Usage:
    monitor = PriceMonitor(symbols, position_manager,
                           on_trade_closed=cb, on_order_filled=cb2)
    monitor.start()
    ...
    monitor.stop()
"""

from __future__ import annotations

import json
import threading
import time
from typing import Callable

from app.config import get_settings
from app.paper.position_manager import PositionManager
from app.utils.logger import get_logger

logger = get_logger(__name__)

POLL_INTERVAL_S   = 60    # seconds between REST checks (fallback mode)
TICK_THROTTLE_S   = 2.0   # min seconds between evaluations per symbol (WS mode)
WS_RETRY_AFTER_S  = 600   # retry the websocket this long after a failure
WS_SILENCE_S      = 30    # connected but no data for this long -> treat as dead
_WS_BASE          = "wss://fstream.binance.com/stream"


class PriceMonitor:
    """
    Daemon thread that watches live prices and triggers SL/TP + pending-order
    checks between 30-m candle closes.

    Parameters
    ----------
    symbols          : Full watchlist — monitor only acts on symbols with open
                       trades or pending orders.
    position_manager : PositionManager instance shared with the watcher.
    on_trade_closed  : Callback(closed_trade: dict, symbol: str).
    on_order_filled  : Callback(order: dict, trade_id: int, symbol: str).
    poll_interval_s  : Seconds between REST polls in fallback mode.
    """

    def __init__(
        self,
        symbols: list[str],
        position_manager: PositionManager,
        on_trade_closed: Callable[[dict, str], None] | None = None,
        on_order_filled: Callable[[dict, int, str], None] | None = None,
        poll_interval_s: int = POLL_INTERVAL_S,
    ) -> None:
        self.symbols          = [s.upper() for s in symbols]
        self._pm              = position_manager
        self._on_closed       = on_trade_closed
        self._on_filled       = on_order_filled
        self._poll_interval_s = poll_interval_s
        self._stop_event      = threading.Event()
        self._thread: threading.Thread | None = None
        self._ws_failed_at    = 0.0
        self._last_eval: dict[str, float] = {}

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background polling thread."""
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="PriceMonitor",
            daemon=True,
        )
        self._thread.start()
        logger.info("[PriceMonitor] Started (websocket primary, REST fallback)")

    def stop(self) -> None:
        """Signal the thread to stop and wait for it to finish."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("[PriceMonitor] Stopped")

    def update_symbols(self, symbols: list[str]) -> None:
        """Update the watchlist (called when rescan changes self.symbols)."""
        self.symbols = [s.upper() for s in symbols]

    # ── Active set ────────────────────────────────────────────────────────────

    def _active_symbols(self) -> list[str]:
        """Symbols that need watching: open trades or pending limit orders."""
        return [
            sym for sym in self.symbols
            if self._pm.open_trades(sym) or self._pm.pending_orders(sym)
        ]

    # ── Tick handling (shared by WS and REST paths) ───────────────────────────

    def _handle_price(self, symbol: str, price: float) -> None:
        if price <= 0:
            return
        # Fill pending limit orders first (an order may open the trade that
        # the very next tick has to manage)
        for ev in self._pm.check_pending(symbol, price, price):
            if ev["outcome"] == "filled" and self._on_filled:
                try:
                    self._on_filled(ev["order"], ev["trade_id"], symbol)
                except Exception as exc:
                    logger.warning("[PriceMonitor] fill callback error: %s", exc)
        for trade in self._pm.check_price(symbol, price):
            if self._on_closed:
                try:
                    self._on_closed(trade, symbol)
                except Exception as exc:
                    logger.warning("[PriceMonitor] close callback error: %s", exc)

    # ── Main loop ─────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                active = self._active_symbols()
            except Exception as exc:
                logger.warning("[PriceMonitor] active-set query failed: %s", exc)
                active = []

            if not active:
                self._sleep(5)
                continue

            ws_ok = (time.time() - self._ws_failed_at) > WS_RETRY_AFTER_S
            if ws_ok:
                try:
                    self._run_ws(active)   # blocks until stop / active change / error
                    continue
                except Exception as exc:
                    self._ws_failed_at = time.time()
                    logger.warning(
                        "[PriceMonitor] WebSocket unavailable (%s) — REST fallback "
                        "for %ds", exc, WS_RETRY_AFTER_S,
                    )

            try:
                self._poll_rest(active)
            except Exception as exc:
                logger.warning("[PriceMonitor] REST poll error: %s", exc)
            self._sleep(self._poll_interval_s)

    def _sleep(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end and not self._stop_event.is_set():
            time.sleep(0.5)

    # ── WebSocket transport ───────────────────────────────────────────────────

    def _run_ws(self, active: list[str]) -> None:
        """
        Stream miniTicker for the active symbols. Returns normally when the
        active set changes or stop is requested; raises on transport errors
        so the caller can fall back to REST.
        """
        import asyncio
        import websockets  # raises ImportError -> REST fallback

        streams = "/".join(f"{s.lower()}@miniTicker" for s in active)
        url = f"{_WS_BASE}?streams={streams}"

        async def _session() -> None:
            async with websockets.connect(
                url, ping_interval=20, ping_timeout=20, close_timeout=5
            ) as ws:
                logger.info("[PriceMonitor] WebSocket connected: %s", ", ".join(active))
                last_recheck = time.time()
                last_data    = time.time()
                while not self._stop_event.is_set():
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=5)
                    except asyncio.TimeoutError:
                        msg = None

                    # Watchdog: a stream that connects but never delivers data
                    # (e.g. regional restriction) must not silently blind us.
                    if msg is None and time.time() - last_data > WS_SILENCE_S:
                        raise RuntimeError(
                            f"no stream data for {WS_SILENCE_S}s (connected but silent)"
                        )

                    if msg:
                        last_data = time.time()
                        try:
                            data = json.loads(msg).get("data", {})
                            sym   = str(data.get("s", "")).upper()
                            price = float(data.get("c", 0) or 0)
                        except (ValueError, AttributeError):
                            continue
                        now = time.time()
                        if sym and now - self._last_eval.get(sym, 0) >= TICK_THROTTLE_S:
                            self._last_eval[sym] = now
                            self._handle_price(sym, price)

                    if time.time() - last_recheck >= 10:
                        last_recheck = time.time()
                        if set(self._active_symbols()) != set(active):
                            logger.info("[PriceMonitor] Active set changed — resubscribing")
                            return

        asyncio.run(_session())

    # ── REST fallback ─────────────────────────────────────────────────────────

    def _poll_rest(self, active: list[str]) -> None:
        from app.data.binance_client import BinanceClient
        with BinanceClient() as client:
            for sym in active:
                if self._stop_event.is_set():
                    return
                try:
                    price = client.get_ticker_price(sym)
                    self._handle_price(sym, price)
                except Exception as exc:
                    logger.warning(
                        "[PriceMonitor][%s] Price check failed: %s", sym, exc
                    )
