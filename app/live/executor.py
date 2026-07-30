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
from app.data.repository import (
    create_live_pending_entry,
    get_live_pending_entries,
    update_live_pending_entry_status,
)
from app.db.connection import get_conn
from app.live.live_notifications import (
    live_notify_error,
    live_notify_partial_tp,
    live_notify_trade_closed,
    live_notify_trade_opened,
)
from app.ta.leverage import dynamic_leverage
from app.utils.logger import get_logger
from app.utils.math_utils import position_size as position_size_fn
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

    def _check_guards(
        self, symbol: str, direction: str, exclude_pending_id: int | None = None
    ) -> tuple[bool, str]:
        """Return (ok, reason). Does NOT acquire lock — caller must hold it.

        exclude_pending_id: when re-checking guards for a resting limit order
        that just filled, its own live_pending_entries row is still status
        'pending' at this point — exclude it so it isn't mistaken for a
        conflicting reservation on itself.
        """
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

            equity = self._client.get_equity()

            # Portfolio open-risk budget — parity with paper's guard 4
            # (position_manager._can_open). The count caps above bound how many
            # positions may be open, not how much they collectively risk.
            open_risk = conn.execute(
                "SELECT SUM(CAST(capital_at_risk AS REAL)) FROM live_trades "
                "WHERE status='open'"
            ).fetchone()[0] or 0.0
            max_open_risk = equity * cfg.max_open_risk_pct / 100
            if open_risk >= max_open_risk:
                return False, (
                    f"open-risk budget (${open_risk:,.2f} at risk ≥ "
                    f"{cfg.max_open_risk_pct:.1f}% of ${equity:,.2f} equity)"
                )

            # Daily loss breaker
            today_start = now_ms - (now_ms % 86_400_000)
            daily_pnl = conn.execute(
                "SELECT SUM(CAST(pnl AS REAL)) FROM live_trades "
                "WHERE status != 'open' AND close_time >= ?",
                (today_start,),
            ).fetchone()[0] or 0.0

            daily_loss_limit = -(equity * cfg.live_max_daily_loss_pct / 100)
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

        # A resting limit entry also reserves the per-symbol slot
        pending = get_live_pending_entries(symbol)
        if exclude_pending_id is not None:
            pending = [p for p in pending if p["id"] != exclude_pending_id]
        if pending:
            return False, f"pending limit entry already placed for {symbol}"

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

    # ── Fill-price resolution ─────────────────────────────────────────────────

    def _resolve_fill_price(self, symbol: str, order_resp: dict) -> float | None:
        """
        Return the average fill price of an order, polling the exchange if the
        synchronous response didn't carry one (common on testnet, sometimes on
        mainnet). Returns None if the price can't be determined.
        """
        price = float(order_resp.get("avgPrice") or 0) or None
        if price is not None:
            return price
        order_id = order_resp.get("orderId")
        if not order_id:
            return None
        for _ in range(5):
            time.sleep(0.5)
            try:
                q = self._client._request("GET", "/fapi/v1/order", {
                    "symbol": symbol, "orderId": order_id,
                })
            except Exception as exc:
                logger.debug("[Executor] fill-price poll %s failed: %s", order_id, exc)
                continue
            price = float(q.get("avgPrice") or 0) or None
            if price is not None:
                return price
        return None

    # ── Entry ─────────────────────────────────────────────────────────────────

    def open_trade(self, symbol: str, sig, signal_id: int | None) -> tuple[str, int | None]:
        """
        Place a bracket order on Binance (market, or a resting limit if
        LIVE_ENTRY_LIMIT_ENABLED and price needs to retrace) and insert a
        live_trades row once filled.

        Returns (outcome, id):
          "opened"  — market entry filled immediately, id = live_trade_id
          "pending" — resting limit placed, id = live_pending_entries id
          "blocked" — guards rejected or entry failed, id = None
        Acquires the shared lock for the entire operation.
        """
        cfg = self._cfg
        with self._lock:
            # 1. Guard checks
            ok, reason = self._check_guards(symbol, sig.signal)
            if not ok:
                logger.info("[Executor] %s BLOCKED — %s", symbol, reason)
                return "blocked", None

            # 2. Live equity → risk amount. Sizing intent is based on stable
            # equity (wallet + unrealized), not availableBalance, which swings
            # with whatever margin other resting orders happen to have locked
            # at this instant. The actual exchange-margin constraint is
            # applied separately below via max_notional.
            equity = self._client.get_equity()
            if equity <= 0:
                logger.warning("[Executor] %s skipped — zero USDT equity", symbol)
                return "blocked", None

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

            # 4. Current mark price — needed both to decide market-vs-limit and,
            # on the market path, to re-validate R:R against drift.
            try:
                exec_price = self._client.get_mark_price(symbol)
            except Exception as exc:
                logger.error("[Executor] %s mark price fetch failed: %s — skip", symbol, exc)
                return "blocked", None
            if exec_price <= 0:
                logger.warning("[Executor] %s mark price unavailable — skip", symbol)
                return "blocked", None

            gap = cfg.entry_limit_min_gap_pct / 100
            needs_retrace = (
                sig.entry_price < exec_price * (1 - gap) if sig.signal == "BUY"
                else sig.entry_price > exec_price * (1 + gap)
            )
            use_limit_entry = cfg.live_entry_limit_enabled and needs_retrace

            # Buffered limit price: rest at whatever price yields target_rr
            # (computed from the signal's own fixed stop/take-profit) rather
            # than the full zone price. Clamp so it never asks for a price
            # better than the zone itself — that's the ceiling on how good a
            # resting price makes sense; only bites if target_rr is
            # misconfigured above the signal's promised R:R.
            buffered_limit_price = buffered_limit_rr = None
            if use_limit_entry:
                target_rr = cfg.live_entry_limit_target_rr
                raw_target = (sig.take_profit + target_rr * sig.stop_loss) / (1 + target_rr)
                if sig.signal == "BUY":
                    buffered_limit_price = max(raw_target, sig.entry_price)
                    buffered_limit_rr = (
                        (sig.take_profit - buffered_limit_price)
                        / (buffered_limit_price - sig.stop_loss)
                    )
                else:
                    buffered_limit_price = min(raw_target, sig.entry_price)
                    buffered_limit_rr = (
                        (buffered_limit_price - sig.take_profit)
                        / (sig.stop_loss - buffered_limit_price)
                    )

            if not use_limit_entry:
                # Entry-price revalidation. The signal's entry_price comes from
                # candle-close analysis and can be far from where a market order
                # will actually fill. Measure risk and reward from the current
                # mark price: if drift has crushed the R:R below the floor, skip
                # rather than take a misshapen trade.
                sl_distance = abs(exec_price - sig.stop_loss)
                if sl_distance == 0:
                    logger.warning("[Executor] %s SL distance is zero — skip", symbol)
                    return "blocked", None
                wrong_side = (
                    exec_price <= sig.stop_loss if sig.signal == "BUY"
                    else exec_price >= sig.stop_loss
                )
                if wrong_side:
                    logger.warning(
                        "[Executor] %s SKIPPED — mark price %.6f already beyond stop %.6f",
                        symbol, exec_price, sig.stop_loss,
                    )
                    return "blocked", None
                reward = (
                    sig.take_profit - exec_price if sig.signal == "BUY"
                    else exec_price - sig.take_profit
                )
                live_rr = reward / sl_distance
                if live_rr < cfg.live_min_rr:
                    logger.warning(
                        "[Executor] %s SKIPPED — R:R at mark price %.6f is %.2f "
                        "(signal entry %.6f promised %.2f, floor %.2f)",
                        symbol, exec_price, live_rr,
                        sig.entry_price, sig.risk_reward or 0, cfg.live_min_rr,
                    )
                    return "blocked", None

            # 5. Position sizing. Market path sizes from the executable (mark)
            # price so capital at risk stays at live_risk_pct of balance even
            # when price has drifted. Limit path sizes from the buffered
            # resting price itself — that's the price the position will
            # actually be risked at once filled.
            sizing_price  = buffered_limit_price if use_limit_entry else exec_price
            intended_qty  = position_size_fn(
                equity, cfg.live_risk_pct, sizing_price, sig.stop_loss, leverage
            )
            raw_qty       = intended_qty
            # Exchange-margin constraint: this must track real free margin
            # (availableBalance), not equity — the exchange rejects orders
            # against margin it doesn't actually have free right now.
            available    = self._client.get_balance()
            max_notional = available * leverage * cfg.live_margin_buffer
            if raw_qty * sizing_price > max_notional:
                clamped = max_notional / sizing_price
                logger.info(
                    "[Executor] %s size clamped for margin headroom: %.6f → %.6f "
                    "(notional cap $%.2f)",
                    symbol, raw_qty, clamped, max_notional,
                )
                raw_qty = clamped
            position_size = self._client.round_qty(symbol, raw_qty)
            if position_size <= 0:
                logger.warning("[Executor] %s position size rounds to zero — skip", symbol)
                return "blocked", None

            # A position clamped to a sliver of its intended size isn't the trade
            # the signal asked for — it pays full fee and spread to carry
            # noise-level exposure and then reports as a real result. Skip it.
            fill_fraction = position_size / intended_qty if intended_qty > 0 else 0.0
            if cfg.live_min_fill_fraction > 0 and fill_fraction < cfg.live_min_fill_fraction:
                logger.warning(
                    "[Executor] %s SKIPPED — margin headroom covers only %.1f%% of the "
                    "intended size (%.6f of %.6f, floor %.0f%%)",
                    symbol, fill_fraction * 100, position_size, intended_qty,
                    cfg.live_min_fill_fraction * 100,
                )
                return "blocked", None

            # Risk of the size actually going on, not the sizing intent. The two
            # diverge by up to 90× once the margin clamp and lot rounding bite,
            # and this figure is what the open-risk budget, R-multiples and every
            # report downstream read. The market path refines it again in
            # _finalize_entry once the true fill price is known; a resting limit
            # fills at its own price, so this value is already exact for it.
            risk_amount = position_size * abs(sizing_price - sig.stop_loss)

            # 6. Preflight exchange checks
            ok, reason = self._preflight(symbol, sig.signal)
            if not ok:
                logger.warning("[Executor] %s preflight failed — %s", symbol, reason)
                return "blocked", None

            # 7. Set leverage on exchange
            try:
                self._client.set_leverage(symbol, leverage)
            except Exception as exc:
                logger.error("[Executor] %s set_leverage failed: %s", symbol, exc)
                return "blocked", None

            entry_side = "BUY" if sig.signal == "BUY" else "SELL"

            if use_limit_entry:
                limit_price = self._client.round_price(symbol, buffered_limit_price)
                try:
                    entry_resp = self._client.place_limit_order(
                        symbol, entry_side, position_size, limit_price
                    )
                except Exception as exc:
                    logger.error("[Executor] %s limit entry failed: %s", symbol, exc)
                    return "blocked", None

                now_ms = _now_ms()
                pending_id = create_live_pending_entry({
                    "symbol":            symbol,
                    "signal_id":         signal_id,
                    "direction":         sig.signal,
                    "limit_price":       limit_price,
                    "stop_loss":         round(sig.stop_loss, 6),
                    "take_profit":       round(sig.take_profit, 6),
                    "position_size":     position_size,
                    "leverage":          leverage,
                    "capital_at_risk":   risk_amount,
                    "risk_reward":       round(buffered_limit_rr, 2),
                    "model_version":     getattr(sig, "model_version", None),
                    "exchange_order_id": str(entry_resp.get("orderId", "")),
                    "created_ms":        now_ms,
                    "expiry_ms":         now_ms + cfg.entry_limit_expiry_candles * 30 * 60 * 1000,
                })
                logger.info(
                    "[Executor] %s Pending %s limit @ %.6f (target R:R %.2f, zone %.6f, "
                    "mark %.6f), expires in %d candles  id=%d",
                    symbol, sig.signal, limit_price, buffered_limit_rr, sig.entry_price,
                    exec_price, cfg.entry_limit_expiry_candles, pending_id,
                )
                return "pending", pending_id

            # 8. Market entry order
            try:
                entry_resp  = self._client.place_market_order(symbol, entry_side, position_size)
            except Exception as exc:
                logger.error("[Executor] %s market entry failed: %s", symbol, exc)
                return "blocked", None

            # The synchronous order response often carries no avgPrice
            # (common on testnet) — poll for the real fill instead of
            # silently using the signal entry: if price has moved past the
            # signal entry, brackets computed from it can sit on the wrong
            # side of the market and Binance rejects them with -2021
            # "Order would immediately trigger" → emergency close.
            actual_fill = self._resolve_fill_price(symbol, entry_resp)
            if actual_fill is None:
                logger.warning(
                    "[Executor] %s entry fill price unavailable after polling — "
                    "falling back to signal entry %.6f for bracket calc",
                    symbol, sig.entry_price,
                )
                actual_fill = sig.entry_price
            exchange_entry_id = str(entry_resp.get("orderId", ""))

            live_trade_id = self._finalize_entry(
                symbol=symbol,
                direction=sig.signal,
                actual_fill=actual_fill,
                stop_loss=sig.stop_loss,
                take_profit=sig.take_profit,
                position_size=position_size,
                leverage=leverage,
                rr=sig.risk_reward or 0,
                signal_id=signal_id,
                model_version=getattr(sig, "model_version", None),
                exchange_entry_id=exchange_entry_id,
            )
            return ("opened", live_trade_id) if live_trade_id is not None else ("blocked", None)

    def _finalize_entry(
        self, symbol: str, direction: str, actual_fill: float, stop_loss: float,
        take_profit: float, position_size: float, leverage: int,
        rr: float, signal_id: int | None,
        model_version: str | None, exchange_entry_id: str,
    ) -> int | None:
        """
        Recompute bracket prices from the actual fill, place SL/TP, and insert
        the live_trades row. Shared by the synchronous market-entry path and
        the async limit-fill handler. Caller must already hold self._lock.

        Capital at risk is derived here rather than passed in: only at this
        point are both the true fill price and the post-clamp, lot-rounded size
        known, and it is the product of the two that the risk budget spends.
        """
        # 9. Recalculate bracket prices from actual fill
        actual_risk    = abs(actual_fill - stop_loss)
        # Stored in two units on purpose, matching paper_trades:
        #   capital_at_risk — USD the stop is worth at this size
        #   original_risk   — the entry→stop price distance, the 1R denominator
        #                     that survives the ratchet mutating stop_loss
        capital_at_risk = position_size * actual_risk
        partial_tp     = (
            actual_fill - 1.5 * actual_risk
            if direction == "SELL"
            else actual_fill + 1.5 * actual_risk
        )
        partial_tp     = self._client.round_price(symbol, partial_tp)
        sl_price       = self._client.round_price(symbol, stop_loss)
        final_tp_price = self._client.round_price(symbol, take_profit)

        # If +1.5R already reaches the final target there is no room for a
        # two-stage exit: a partial TP clamped to the final TP means the
        # replacement TP after the partial fill lands at the same price and
        # Binance rejects it with -2021 "would immediately trigger". Take
        # the whole position off at the final TP in one order instead.
        single_stage = (
            partial_tp <= final_tp_price if direction == "SELL"
            else partial_tp >= final_tp_price
        )

        half_size      = self._client.round_qty(symbol, position_size / 2)
        # Round the raw difference before lot-snapping: float dust
        # (37.33 - 18.66 = 18.669999…) otherwise gets floored one whole
        # lot-step short, leaving a residual position behind the brackets.
        remaining_size = self._client.round_qty(
            symbol, round(position_size - half_size, 8)
        )

        # Bracket sides are always the opposite of the entry
        bracket_side = "SELL" if direction == "BUY" else "BUY"

        # 10. Place bracket orders
        sl_order = tp_order = None
        try:
            sl_order = self._client.place_stop_market(
                symbol, bracket_side, sl_price, position_size
            )
            if single_stage:
                tp_order = self._client.place_take_profit_market(
                    symbol, bracket_side, final_tp_price, position_size
                )
            else:
                tp_order = self._client.place_take_profit_market(
                    symbol, bracket_side, partial_tp, half_size
                )
        except Exception as exc:
            logger.error("[Executor] %s bracket placement failed: %s — emergency close", symbol, exc)
            # Cancel whatever was placed
            if sl_order:
                self._client.cancel_algo_order(symbol, sl_order.get("algoId", ""))
            # Close position immediately
            close_side = "SELL" if direction == "BUY" else "BUY"
            try:
                self._client.place_market_order(
                    symbol, close_side, position_size, reduce_only=True
                )
            except Exception as close_exc:
                logger.error("[Executor] %s emergency close failed: %s", symbol, close_exc)
            return None

        # 11. Insert live_trades row
        # Single-stage exits are stored with partial_taken=1 and the full
        # size as remaining: a fill of the TP algo then routes straight to
        # handle_tp_hit and the whole position closes as target_hit.
        open_time = _now_ms()

        with get_conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO live_trades (
                    symbol, signal_id, direction, status,
                    entry_price, stop_loss, take_profit, partial_tp_price,
                    position_size, remaining_size, capital_at_risk,
                    risk_reward, leverage, open_time, partial_taken,
                    exchange_entry_id, exchange_sl_id, exchange_tp_id,
                    original_risk, model_version
                ) VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol, signal_id, direction,
                    actual_fill, sl_price, final_tp_price,
                    final_tp_price if single_stage else partial_tp,
                    position_size,
                    position_size if single_stage else remaining_size,
                    capital_at_risk,
                    rr, leverage, open_time,
                    1 if single_stage else 0,
                    exchange_entry_id,
                    str(sl_order.get("algoId", "")),
                    str(tp_order.get("algoId", "")),
                    round(actual_risk, 6),
                    model_version,
                ),
            )
            live_trade_id = cur.lastrowid
            conn.commit()

        logger.info(
            "[Executor] %s %s opened  fill=%.4f  sl=%.4f  %s=%.4f  "
            "size=%.6f  lev=%d×  id=%d",
            symbol, direction, actual_fill, sl_price,
            "single_tp" if single_stage else "partial_tp",
            final_tp_price if single_stage else partial_tp,
            position_size, leverage, live_trade_id,
        )
        return live_trade_id

    # ── Fill handlers (called from UserDataStream with lock already held) ──────

    def handle_entry_limit_fill(
        self, pending_id: int, fill_price: float, fill_qty: float,
    ) -> None:
        """
        A resting entry limit order filled. Unlike a simulated paper fill this
        is irreversible, so guards are re-checked before committing: if the
        symbol is no longer allowed (cooldown/cap/daily-loss tripped while the
        order sat resting), the fresh position is market-flattened immediately
        rather than left open in violation of the guard it would have failed.
        """
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM live_pending_entries WHERE id=?", (pending_id,)
            ).fetchone()
        if not row:
            logger.warning("[Executor] handle_entry_limit_fill: pending entry %d not found", pending_id)
            return
        pending = dict(row)
        symbol    = pending["symbol"]
        direction = pending["direction"]
        position_size = float(pending["position_size"])

        ok, reason = self._check_guards(symbol, direction, exclude_pending_id=pending_id)
        if not ok:
            logger.warning(
                "[Executor] %s limit entry filled but guard now fails (%s) — "
                "flattening  id=%d", symbol, reason, pending_id,
            )
            close_side = "SELL" if direction == "BUY" else "BUY"
            try:
                self._client.place_market_order(symbol, close_side, position_size, reduce_only=True)
            except Exception as exc:
                logger.critical(
                    "[Executor] %s flatten of stale-guard fill FAILED: %s — "
                    "position may still be open and UNPROTECTED, manual intervention required  "
                    "pending_id=%d", symbol, exc, pending_id,
                )
                live_notify_error(
                    f"{symbol} stale-guard fill — manual intervention required",
                    f"Limit entry filled after guard '{reason}' tripped and the emergency "
                    f"flatten also failed: {exc} — pending_id={pending_id}, "
                    f"position may still be open and UNPROTECTED.",
                )
                return
            update_live_pending_entry_status(pending_id, "flattened")
            live_notify_error(
                f"{symbol} limit entry flattened",
                f"Filled @ {fill_price:.6f} but guard '{reason}' tripped while the order was "
                f"resting — position closed immediately rather than left open.",
            )
            return

        live_trade_id = self._finalize_entry(
            symbol=symbol,
            direction=direction,
            actual_fill=fill_price,
            stop_loss=float(pending["stop_loss"]),
            take_profit=float(pending["take_profit"]),
            position_size=position_size,
            leverage=int(pending["leverage"]),
            rr=float(pending["risk_reward"] or 0),
            signal_id=pending.get("signal_id"),
            model_version=pending.get("model_version"),
            exchange_entry_id=pending["exchange_order_id"],
        )
        update_live_pending_entry_status(
            pending_id, "filled" if live_trade_id else "cancelled", live_trade_id
        )
        if live_trade_id is not None:
            with get_conn() as conn:
                trade_row = conn.execute(
                    "SELECT * FROM live_trades WHERE id=?", (live_trade_id,)
                ).fetchone()
            if trade_row:
                live_notify_trade_opened(
                    dict(trade_row), symbol, leverage=int(pending["leverage"]),
                )

    def check_pending_entries(self) -> None:
        """
        Cancel resting limit entries past expiry. Runs on the main thread and
        holds self._lock for its full duration, same as open_trade — this is
        what makes a fill-vs-expire race safe: UserDataStream's _dispatch_fill
        acquires the same lock before touching any pending entry, so a fill
        event can never interleave with an expiry cancel for the same order.
        """
        now_ms = _now_ms()
        with self._lock:
            for entry in get_live_pending_entries():
                if now_ms < int(entry["expiry_ms"]):
                    continue
                result = self._client.cancel_order(entry["symbol"], entry["exchange_order_id"])
                if result:
                    update_live_pending_entry_status(entry["id"], "expired")
                    logger.info(
                        "[Executor] %s pending entry #%d expired and cancelled",
                        entry["symbol"], entry["id"],
                    )
                else:
                    # Cancel failed/ambiguous — on Binance this usually means the
                    # order already filled (or was already gone). Don't guess:
                    # leave it 'pending' so a genuine fill event or the next
                    # sweep resolves it correctly.
                    logger.warning(
                        "[Executor] %s pending entry #%d cancel-at-expiry returned "
                        "no result — leaving status alone pending resolution",
                        entry["symbol"], entry["id"],
                    )

    def update_excursion(self, symbol: str, candle_high: float, candle_low: float) -> None:
        """
        Update the running max favorable/adverse excursion for an open live
        trade from the latest 30m candle's high/low. Called once per symbol
        per cycle from the main thread (live_watcher already fetches this
        candle for signal generation — no extra API calls). Mirrors the
        formula PositionManager.check_price() uses for paper
        (app/paper/position_manager.py:699-709), but samples candle high/low
        instead of tick close — live has no continuous tick stream, so this
        is the closest equivalent and is actually a tighter bound on true
        intra-candle excursion than paper's tick-sampled version.
        """
        with self._lock:
            with get_conn() as conn:
                row = conn.execute(
                    "SELECT id, direction, entry_price, max_favorable_excursion, "
                    "max_adverse_excursion FROM live_trades WHERE symbol=? AND status='open'",
                    (symbol,),
                ).fetchone()
            if not row:
                return
            trade_id, direction, entry, stored_mfe, stored_mae = row
            entry      = float(entry)
            stored_mfe = float(stored_mfe or 0)
            stored_mae = float(stored_mae or 0)

            if direction == "BUY":
                curr_mfe = max(0.0, candle_high - entry)
                curr_mae = max(0.0, entry - candle_low)
            else:
                curr_mfe = max(0.0, entry - candle_low)
                curr_mae = max(0.0, candle_high - entry)

            new_mfe = max(stored_mfe, curr_mfe)
            new_mae = max(stored_mae, curr_mae)
            if new_mfe == stored_mfe and new_mae == stored_mae:
                return

            with get_conn() as conn:
                conn.execute(
                    "UPDATE live_trades SET max_favorable_excursion=?, "
                    "max_adverse_excursion=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (round(new_mfe, 6), round(new_mae, 6), trade_id),
                )
                conn.commit()

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

        # The exchange position is the source of truth for what is actually
        # left after the partial fill — the DB's remaining_size can sit a
        # lot-step away from it and leave dust behind the new bracket.
        try:
            pos = self._client.get_position(symbol)
            if pos is not None:
                exchange_amt = abs(float(pos.get("positionAmt", 0)))
                if exchange_amt > 0:
                    remaining = exchange_amt
        except Exception as exc:
            logger.warning(
                "[Executor] %s position query after partial TP failed: %s — "
                "using DB remaining_size %.6f", symbol, exc, remaining,
            )

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
            # -2021 "Order would immediately trigger" is not an execution
            # fault: price has already moved through the trigger, so the
            # remainder just needs to come off at market. If the final TP was
            # the order that bounced (BE-SL went in fine), the target was
            # effectively reached.
            body = getattr(getattr(exc, "response", None), "text", "") or ""
            target_reached = "-2021" in body and new_sl is not None
            if target_reached:
                logger.info(
                    "[Executor] %s final TP already beyond price after partial — "
                    "closing remainder at market", symbol,
                )
            else:
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
                close_resp  = self._client.place_market_order(
                    symbol, close_side, remaining, reduce_only=True
                )
                close_price = self._resolve_fill_price(symbol, close_resp)
            except Exception as close_exc:
                logger.critical(
                    "[Executor] %s emergency close of naked remainder FAILED: %s — "
                    "position may still be open and UNPROTECTED, manual intervention required  id=%d",
                    symbol, close_exc, live_trade_id,
                )
                live_notify_error(
                    f"{symbol} naked remainder — manual intervention required",
                    f"Partial TP bracket replace failed and emergency close also failed: "
                    f"{close_exc} — id={live_trade_id}, position may still be open and UNPROTECTED.",
                )

            if close_price is not None:
                if direction == "BUY":
                    remain_pnl = (close_price - entry_price) * remaining
                else:
                    remain_pnl = (entry_price - close_price) * remaining
                total_pnl    = partial_pnl + remain_pnl
                close_status = "target_hit" if target_reached else "closed"
            else:
                total_pnl    = None
                close_status = "open"

            equity  = self._client.get_equity()
            pnl_pct = (total_pnl / equity * 100) if (total_pnl is not None and equity) else 0.0

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
                        pnl_pct        = ?,
                        exchange_sl_id = '',
                        exchange_tp_id = '',
                        updated_at     = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        partial_pnl,
                        close_status,
                        close_price,
                        _now_ms() if close_price is not None else None,
                        total_pnl,
                        pnl_pct,
                        live_trade_id,
                    ),
                )
                conn.commit()

            if close_price is not None:
                live_notify_trade_closed(
                    {
                        "direction": direction,
                        "entry_price": entry_price,
                        "close_price": close_price,
                        "pnl": total_pnl,
                        "pnl_pct": pnl_pct,
                        "status": close_status,
                    },
                    symbol, leverage=int(trade.get("leverage") or 1),
                )

            log = logger.info if target_reached else logger.warning
            log(
                "[Executor] %s partial TP filled, remainder market-closed [%s]  "
                "pnl=%s  id=%d",
                symbol, close_status,
                f"{total_pnl:.2f}" if total_pnl is not None else "UNKNOWN", live_trade_id,
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

        live_notify_partial_tp(
            {
                "remaining_size": remaining,
                "take_profit": final_tp,
                "entry_price": entry_price,
            },
            symbol, fill_price, partial_pnl,
        )

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
        equity = self._client.get_equity()
        pnl_pct = (total_pnl / equity * 100) if equity else 0.0

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

        live_notify_trade_closed(
            {
                "direction": direction,
                "entry_price": entry_price,
                "close_price": close_price,
                "pnl": total_pnl,
                "pnl_pct": pnl_pct,
                "status": status,
            },
            symbol, leverage=leverage,
        )

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
                        self._client.place_market_order(symbol, close_side, qty, reduce_only=True)
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
