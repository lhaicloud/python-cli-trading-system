"""
Live trade lifecycle manager.

Opens bracket orders on Binance Futures, handles fill events from UserDataStream,
manages the live_trades table, and enforces circuit breakers independent from paper.

Thread safety: every method that touches the DB or exchange must be called with
the shared threading.Lock held, OR is safe to call from the main thread only.
Methods that say "acquire lock" in their docstring may be called from the
UserDataStream thread — they acquire the lock themselves.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

from app.config import get_settings
from app.db.connection import get_conn
from app.ta.leverage import dynamic_leverage
from app.utils.logger import get_logger
from app.utils.timeframes import ms_to_dt

if TYPE_CHECKING:
    from app.live.exchange import BinanceExchangeClient

logger = get_logger(__name__)


def _now_ms() -> int:
    return int(time.time() * 1000)


class LiveExecutor:
    """
    Manages the live trade lifecycle:  open → partial TP → SL/TP close.

    Parameters
    ----------
    client   : BinanceExchangeClient
    lock     : threading.Lock  — shared with UserDataStream
    """

    def __init__(self, client: BinanceExchangeClient, lock: threading.Lock) -> None:
        self._client = client
        self._lock   = lock
        self._cfg    = get_settings()

    # ── Guard checks ─────────────────────────────────────────────────────────

    def _check_guards(self, symbol: str, direction: str) -> tuple[bool, str]:
        """Return (ok, reason). Does NOT acquire lock — caller must hold it."""
        cfg = self._cfg
        now_ms = _now_ms()

        with get_conn() as conn:
            # Portfolio cap
            open_count = conn.execute(
                "SELECT COUNT(*) FROM live_trades WHERE status='open'"
            ).fetchone()[0]
            if open_count >= cfg.live_max_portfolio:
                return False, f"portfolio cap ({open_count}/{cfg.live_max_portfolio})"

            # Same-direction cap
            dir_count = conn.execute(
                "SELECT COUNT(*) FROM live_trades WHERE status='open' AND direction=?",
                (direction,),
            ).fetchone()[0]
            if dir_count >= cfg.live_max_same_dir:
                return False, f"same-dir cap {direction} ({dir_count}/{cfg.live_max_same_dir})"

            # Daily loss breaker
            today_start = now_ms - (now_ms % 86_400_000)
            daily_pnl = conn.execute(
                "SELECT SUM(CAST(pnl AS REAL)) FROM live_trades "
                "WHERE status != 'open' AND close_time >= ?",
                (today_start,),
            ).fetchone()[0] or 0.0

            balance = self._client.get_balance()
            daily_loss_limit = -(balance * cfg.live_max_daily_loss_pct / 100)
            if daily_pnl <= daily_loss_limit:
                return False, f"daily loss breaker (PnL={daily_pnl:.2f} ≤ {daily_loss_limit:.2f})"

            # Stop cooldown — no new entry if symbol had a stop in last 10h
            cooldown_ms = 10 * 3_600_000
            recent_stop = conn.execute(
                "SELECT COUNT(*) FROM live_trades "
                "WHERE symbol=? AND status='stopped' AND close_time >= ?",
                (symbol, now_ms - cooldown_ms),
            ).fetchone()[0]
            if recent_stop:
                return False, f"stop cooldown for {symbol} (stopped within 10h)"

            # No existing open trade for this symbol
            existing = conn.execute(
                "SELECT COUNT(*) FROM live_trades WHERE symbol=? AND status='open'",
                (symbol,),
            ).fetchone()[0]
            if existing:
                return False, f"already have open live trade for {symbol}"

        return True, ""

    def _preflight(self, symbol: str, direction: str) -> tuple[bool, str]:
        """Exchange-level checks. Does NOT acquire lock — caller must hold it."""
        # Position mode must be one-way
        mode = self._client.get_position_mode()
        if mode == "hedge":
            return False, "Account is in HEDGE mode — switch to one-way mode and restart"

        # No existing position on exchange
        pos = self._client.get_position(symbol)
        if pos is not None:
            return False, f"Exchange already has position for {symbol}: {pos.get('positionAmt')}"

        return True, ""

    # ── Entry ─────────────────────────────────────────────────────────────────

    def open_trade(self, symbol: str, sig, signal_id: int | None) -> int | None:
        """
        Place a full bracket order on Binance and insert a live_trades row.

        Returns the new live_trade id, or None if entry was blocked/failed.
        Acquires the shared lock for the entire operation.
        """
        cfg = self._cfg
        with self._lock:
            # 1. Guard checks
            ok, reason = self._check_guards(symbol, sig.signal)
            if not ok:
                logger.info("[Executor] %s BLOCKED — %s", symbol, reason)
                return None

            # 2. Live balance → risk amount
            balance = self._client.get_balance()
            if balance <= 0:
                logger.warning("[Executor] %s skipped — zero USDT balance", symbol)
                return None

            # 3. Leverage
            with get_conn() as conn:
                recent_rows = conn.execute(
                    "SELECT pnl FROM live_trades WHERE symbol=? AND status!='open' "
                    "ORDER BY close_time DESC LIMIT 5",
                    (symbol,),
                ).fetchall()
            recent_trades = [{"pnl": float(r[0] or 0)} for r in recent_rows]
            leverage = dynamic_leverage(
                sig, recent_trades, max_leverage=cfg.live_max_leverage
            )

            # 4. Position sizing
            risk_amount   = balance * cfg.live_risk_pct / 100
            sl_distance   = abs(sig.entry_price - sig.stop_loss)
            if sl_distance == 0:
                logger.warning("[Executor] %s SL distance is zero — skip", symbol)
                return None

            notional      = (risk_amount / sl_distance) * sig.entry_price * leverage
            raw_qty       = notional / sig.entry_price
            position_size = self._client.round_qty(symbol, raw_qty)
            if position_size <= 0:
                logger.warning("[Executor] %s position size rounds to zero — skip", symbol)
                return None

            # 5. Preflight exchange checks
            ok, reason = self._preflight(symbol, sig.signal)
            if not ok:
                logger.warning("[Executor] %s preflight failed — %s", symbol, reason)
                return None

            # 6. Set leverage on exchange
            try:
                self._client.set_leverage(symbol, leverage)
            except Exception as exc:
                logger.error("[Executor] %s set_leverage failed: %s", symbol, exc)
                return None

            # 7. Market entry order
            entry_side = "BUY" if sig.signal == "BUY" else "SELL"
            try:
                entry_resp  = self._client.place_market_order(symbol, entry_side, position_size)
            except Exception as exc:
                logger.error("[Executor] %s market entry failed: %s", symbol, exc)
                return None

            actual_fill = float(entry_resp.get("avgPrice") or sig.entry_price)
            exchange_entry_id = str(entry_resp.get("orderId", ""))

            # 8. Recalculate bracket prices from actual fill
            actual_risk    = abs(actual_fill - sig.stop_loss)
            partial_tp     = (
                actual_fill - 1.5 * actual_risk
                if sig.signal == "SELL"
                else actual_fill + 1.5 * actual_risk
            )
            partial_tp     = self._client.round_price(symbol, partial_tp)
            sl_price       = self._client.round_price(symbol, sig.stop_loss)
            final_tp_price = self._client.round_price(symbol, sig.take_profit)

            half_size      = self._client.round_qty(symbol, position_size / 2)
            remaining_size = self._client.round_qty(symbol, position_size - half_size)

            # Bracket sides are always the opposite of the entry
            bracket_side = "SELL" if sig.signal == "BUY" else "BUY"

            # 9. Place bracket orders
            sl_order = tp_order = None
            try:
                sl_order = self._client.place_stop_market(
                    symbol, bracket_side, sl_price, position_size
                )
                tp_order = self._client.place_take_profit_market(
                    symbol, bracket_side, partial_tp, half_size
                )
            except Exception as exc:
                logger.error("[Executor] %s bracket placement failed: %s — emergency close", symbol, exc)
                # Cancel whatever was placed
                if sl_order:
                    self._client.cancel_algo_order(symbol, sl_order.get("algoId", ""))
                # Close position immediately
                close_side = "SELL" if sig.signal == "BUY" else "BUY"
                try:
                    self._client.place_market_order(symbol, close_side, position_size)
                except Exception as close_exc:
                    logger.error("[Executor] %s emergency close failed: %s", symbol, close_exc)
                return None

            # 10. Insert live_trades row
            capital_at_risk = risk_amount
            rr = sig.risk_reward or 0
            open_time = _now_ms()

            with get_conn() as conn:
                cur = conn.execute(
                    """
                    INSERT INTO live_trades (
                        symbol, signal_id, direction, status,
                        entry_price, stop_loss, take_profit, partial_tp_price,
                        position_size, remaining_size, capital_at_risk,
                        risk_reward, leverage, open_time,
                        exchange_entry_id, exchange_sl_id, exchange_tp_id,
                        original_risk, model_version
                    ) VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        symbol, signal_id, sig.signal,
                        actual_fill, sl_price, final_tp_price, partial_tp,
                        position_size, remaining_size, capital_at_risk,
                        rr, leverage, open_time,
                        exchange_entry_id,
                        str(sl_order.get("algoId", "")),
                        str(tp_order.get("algoId", "")),
                        capital_at_risk,
                        getattr(sig, "model_version", None),
                    ),
                )
                live_trade_id = cur.lastrowid
                conn.commit()

            logger.info(
                "[Executor] %s %s opened  fill=%.4f  sl=%.4f  partial_tp=%.4f  "
                "size=%.6f  lev=%d×  id=%d",
                symbol, sig.signal, actual_fill, sl_price, partial_tp,
                position_size, leverage, live_trade_id,
            )
            return live_trade_id

    # ── Fill handlers (called from UserDataStream with lock already held) ──────

    def handle_partial_tp(
        self,
        live_trade_id: int,
        fill_price: float,
        filled_qty: float,
    ) -> None:
        """Partial TP filled — replace bracket with BE-SL + final TP."""
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM live_trades WHERE id=?", (live_trade_id,)
            ).fetchone()
            if not row:
                logger.warning("[Executor] handle_partial_tp: trade %d not found", live_trade_id)
                return
            trade = dict(row)

        symbol      = trade["symbol"]
        direction   = trade["direction"]
        entry_price = float(trade["entry_price"])
        remaining   = float(trade["remaining_size"])
        final_tp    = float(trade["take_profit"])
        old_sl_id   = trade["exchange_sl_id"]
        old_tp_id   = trade["exchange_tp_id"]

        # Partial PnL
        if direction == "BUY":
            partial_pnl = (fill_price - entry_price) * filled_qty
        else:
            partial_pnl = (entry_price - fill_price) * filled_qty

        # Cancel original SL
        if old_sl_id:
            self._client.cancel_algo_order(symbol, old_sl_id)

        # New bracket: BE stop + final TP
        bracket_side = "SELL" if direction == "BUY" else "BUY"
        be_price     = self._client.round_price(symbol, entry_price)  # break-even = actual fill
        final_tp_r   = self._client.round_price(symbol, final_tp)

        new_sl = new_tp = None
        try:
            new_sl = self._client.place_stop_market(symbol, bracket_side, be_price, remaining)
            new_tp = self._client.place_take_profit_market(symbol, bracket_side, final_tp_r, remaining)
        except Exception as exc:
            logger.error("[Executor] %s bracket replace after partial TP failed: %s", symbol, exc)
            if new_sl:
                self._client.cancel_algo_order(symbol, new_sl.get("algoId", ""))

            # Remaining size now has no bracket resting on the exchange —
            # market-close it immediately rather than leaving it naked
            # (e.g. price already crossed break-even by the time we tried
            # to replace the bracket, so Binance rejects the new SL with
            # -2021 "Order would immediately trigger").
            close_side  = "SELL" if direction == "BUY" else "BUY"
            close_price = None
            try:
                close_resp  = self._client.place_market_order(symbol, close_side, remaining)
                close_price = float(close_resp.get("avgPrice") or 0) or None
            except Exception as close_exc:
                logger.critical(
                    "[Executor] %s emergency close of naked remainder FAILED: %s — "
                    "position may still be open and UNPROTECTED, manual intervention required  id=%d",
                    symbol, close_exc, live_trade_id,
                )

            if close_price is not None:
                if direction == "BUY":
                    remain_pnl = (close_price - entry_price) * remaining
                else:
                    remain_pnl = (entry_price - close_price) * remaining
                total_pnl = partial_pnl + remain_pnl
            else:
                total_pnl = None

            with get_conn() as conn:
                conn.execute(
                    """
                    UPDATE live_trades SET
                        partial_taken  = 1,
                        partial_pnl    = ?,
                        status         = ?,
                        close_price    = ?,
                        close_time     = ?,
                        pnl            = ?,
                        exchange_sl_id = '',
                        exchange_tp_id = '',
                        updated_at     = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        partial_pnl,
                        "closed" if close_price is not None else "open",
                        close_price,
                        _now_ms() if close_price is not None else None,
                        total_pnl,
                        live_trade_id,
                    ),
                )
                conn.commit()

            logger.warning(
                "[Executor] %s partial TP filled but bracket replace failed — "
                "remainder emergency-closed  pnl=%s  id=%d",
                symbol, f"{total_pnl:.2f}" if total_pnl is not None else "UNKNOWN", live_trade_id,
            )
            return

        with get_conn() as conn:
            conn.execute(
                """
                UPDATE live_trades SET
                    partial_taken    = 1,
                    partial_pnl      = ?,
                    exchange_sl_id   = ?,
                    exchange_tp_id   = ?,
                    updated_at       = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    partial_pnl,
                    str(new_sl.get("algoId", "")) if new_sl else "",
                    str(new_tp.get("algoId", "")) if new_tp else "",
                    live_trade_id,
                ),
            )
            conn.commit()

        logger.info(
            "[Executor] %s partial TP @ %.4f  qty=%.6f  pnl=%.2f  BE-SL=%s  finalTP=%s",
            symbol, fill_price, filled_qty, partial_pnl,
            new_sl.get("algoId", "?") if new_sl else "FAILED",
            new_tp.get("algoId", "?") if new_tp else "FAILED",
        )

    def handle_sl_hit(self, live_trade_id: int, fill_price: float) -> None:
        """SL filled — cancel remaining TP and close the trade row."""
        self._close_trade(live_trade_id, fill_price, "stopped")

    def handle_tp_hit(self, live_trade_id: int, fill_price: float) -> None:
        """Final TP filled — cancel remaining SL and close the trade row."""
        self._close_trade(live_trade_id, fill_price, "target_hit")

    def _close_trade(self, live_trade_id: int, close_price: float, status: str) -> None:
        """Close a live_trades row and cancel remaining bracket order."""
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM live_trades WHERE id=?", (live_trade_id,)
            ).fetchone()
            if not row:
                return
            trade = dict(row)

        symbol      = trade["symbol"]
        direction   = trade["direction"]
        entry_price = float(trade["entry_price"])
        remaining   = float(trade["remaining_size"])
        partial_pnl = float(trade.get("partial_pnl") or 0)
        leverage    = int(trade.get("leverage") or 1)
        sl_id       = trade.get("exchange_sl_id")
        tp_id       = trade.get("exchange_tp_id")

        # Cancel whichever bracket order is still resting
        other_id = tp_id if status == "stopped" else sl_id
        if other_id:
            self._client.cancel_algo_order(symbol, other_id)

        # Remaining PnL
        if direction == "BUY":
            remain_pnl = (close_price - entry_price) * remaining
        else:
            remain_pnl = (entry_price - close_price) * remaining

        total_pnl = partial_pnl + remain_pnl
        risk = float(trade.get("capital_at_risk") or 1)
        pnl_pct = (total_pnl / risk * 100) if risk else 0.0

        close_time = _now_ms()
        with get_conn() as conn:
            conn.execute(
                """
                UPDATE live_trades SET
                    status     = ?,
                    close_price= ?,
                    close_time = ?,
                    pnl        = ?,
                    pnl_pct    = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (status, close_price, close_time, total_pnl, pnl_pct, live_trade_id),
            )
            conn.commit()

        result = "WIN" if total_pnl >= 0 else "LOSS"
        logger.info(
            "[Executor] %s %s [%s]  fill=%.4f  pnl=%.2f (%.2f%%)  id=%d",
            symbol, status.upper(), result, close_price, total_pnl, pnl_pct, live_trade_id,
        )

    # ── Candle-close fallback ─────────────────────────────────────────────────

    def sync_open_orders(self) -> None:
        """
        Belt-and-suspenders check each cycle: for every open live_trade verify
        bracket orders are still resting on the exchange. If both are gone,
        query order history to find the fill and close the DB row.

        Call from the main watcher thread (not UserDataStream) — acquires lock.
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
                if not sl_id and not tp_id:
                    continue

                try:
                    open_orders = self._client.get_open_algo_orders(symbol)
                except Exception as exc:
                    logger.warning("[Executor] sync_open_orders %s fetch failed: %s", symbol, exc)
                    continue

                open_ids = {str(o["algoId"]) for o in open_orders}
                sl_present = sl_id in open_ids
                tp_present = tp_id in open_ids

                if sl_present or tp_present:
                    continue  # at least one bracket still resting — ok

                # Both are gone — determine which filled last
                logger.warning(
                    "[Executor] %s id=%d — bracket orders gone from exchange, closing via sync",
                    symbol, trade["id"],
                )
                # Heuristic: check order history for fill side
                # (simplified — in practice, query /fapi/v1/order for each id)
                self._close_trade_by_history(trade)

    def _close_trade_by_history(self, trade: dict) -> None:
        """Query order history for a trade and close accordingly."""
        symbol = trade["symbol"]
        sl_id  = str(trade.get("exchange_sl_id") or "")
        tp_id  = str(trade.get("exchange_tp_id") or "")

        for order_id, status_name in [(sl_id, "stopped"), (tp_id, "target_hit")]:
            if not order_id:
                continue
            try:
                resp = self._client.get_algo_order(symbol, order_id)
                if resp.get("algoStatus") == "FINISHED":
                    fill = float(resp.get("actualPrice") or resp.get("triggerPrice") or 0)
                    qty  = float(resp.get("actualQty") or 0)
                    if fill and qty:
                        self._close_trade(trade["id"], fill, status_name)
                        return
            except Exception as exc:
                logger.debug("[Executor] order history query %s failed: %s", order_id, exc)

        logger.warning("[Executor] Could not determine fill for trade %d — left open", trade["id"])

    # ── Emergency close ───────────────────────────────────────────────────────

    def emergency_close(self, symbol: str) -> float:
        """
        Cancel all open orders for symbol and market-close any position.

        Acquires the lock. Returns realised PnL or 0.0.
        """
        with self._lock:
            self._client.cancel_all_orders(symbol)

            # cancel_all_orders only purges the regular order book — algo/
            # conditional (bracket) orders live in a separate order book and
            # must be cancelled explicitly, or a stale SL/TP could later
            # trigger against a new position on the same symbol.
            try:
                for algo in self._client.get_open_algo_orders(symbol):
                    self._client.cancel_algo_order(symbol, algo.get("algoId", ""))
            except Exception as exc:
                logger.warning("[Executor] emergency_close %s algo cleanup failed: %s", symbol, exc)

            pos = self._client.get_position(symbol)
            if pos is None:
                logger.info("[Executor] emergency_close %s — no position on exchange", symbol)
            else:
                pos_amt = float(pos.get("positionAmt", 0))
                close_side = "SELL" if pos_amt > 0 else "BUY"
                qty = abs(pos_amt)
                qty = self._client.round_qty(symbol, qty)
                if qty > 0:
                    try:
                        self._client.place_market_order(symbol, close_side, qty)
                    except Exception as exc:
                        logger.error("[Executor] emergency market close %s failed: %s", symbol, exc)

            # Close DB row(s) for this symbol
            total_pnl = 0.0
            with get_conn() as conn:
                rows = [
                    dict(r) for r in conn.execute(
                        "SELECT * FROM live_trades WHERE symbol=? AND status='open'",
                        (symbol,),
                    ).fetchall()
                ]
            for trade in rows:
                close_ms = _now_ms()
                entry    = float(trade.get("entry_price") or 0)
                # Use last candle price as proxy for close price
                try:
                    from app.data.repository import get_candles
                    df = get_candles(symbol, "30m", limit=1)
                    close_price = float(df["close"].iloc[-1]) if not df.empty else entry
                except Exception:
                    close_price = entry

                direction = trade.get("direction", "BUY")
                remaining = float(trade.get("remaining_size") or trade.get("position_size") or 0)
                partial_pnl = float(trade.get("partial_pnl") or 0)
                remain_pnl = (
                    (close_price - entry) * remaining
                    if direction == "BUY"
                    else (entry - close_price) * remaining
                )
                pnl = partial_pnl + remain_pnl
                total_pnl += pnl

                with get_conn() as conn:
                    conn.execute(
                        """
                        UPDATE live_trades SET
                            status='closed', close_price=?, close_time=?,
                            pnl=?, updated_at=CURRENT_TIMESTAMP
                        WHERE id=?
                        """,
                        (close_price, close_ms, pnl, trade["id"]),
                    )
                    conn.commit()

            logger.info("[Executor] emergency_close %s complete  pnl=%.2f", symbol, total_pnl)
            return total_pnl

    # ── Queries ───────────────────────────────────────────────────────────────

    def open_trades(self, symbol: str | None = None) -> list[dict]:
        sql = "SELECT * FROM live_trades WHERE status='open'"
        params: list = []
        if symbol:
            sql += " AND symbol=?"
            params.append(symbol)
        with get_conn() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def all_open_trades(self) -> list[dict]:
        return self.open_trades()
