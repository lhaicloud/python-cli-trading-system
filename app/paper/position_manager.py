"""
PositionManager — single owner of all live/paper trade lifecycle.

All state transitions (open → stopped / target_hit / closed) go through
this class. Nothing else writes paper_trades directly in live mode.

Risk model (portfolio-wide, cross-process safe — all guards query the DB):
  - sizing reads the unified portfolio equity, not per-symbol silos
  - total open risk capped at cfg.max_open_risk_pct of equity
  - same-direction positions get scaled-down risk (cfg.corr_risk_scale)
  - daily loss circuit breaker on realized portfolio PnL (UTC day)

Entry model:
  - submit() places a simulated resting limit at the signal's entry price
    when price still needs to retrace to it (kills the MFE=0 instant losers);
    fills are detected by check_pending() from candle/tick data
  - market entry only when price is already at the entry level
"""

from __future__ import annotations

import dataclasses
import threading
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app.config import get_settings
from app.data.repository import (
    add_zone_blacklist,
    apply_partial_tp,
    close_paper_trade,
    create_pending_order,
    get_all_open_paper_trades,
    get_closed_pnl_since,
    get_last_stopped_trade_ms,
    get_open_paper_trades,
    get_pending_orders,
    get_portfolio_stops_in_window,
    get_recent_closed_paper_trades,
    get_recent_stops_in_window,
    get_symbol_risk_stats,
    is_zone_blacklisted,
    open_paper_trade,
    save_feature_snapshot,
    update_open_paper_trade_pnl,
    update_paper_trade_stop,
    update_pending_order_status,
)
from app.paper.account import (
    get_portfolio_capital,
    update_capital_after_trade,
)
from app.paper.broker import simulate_fill
from app.ta.exit_engine import apply_ratchet, build_ratchet_levels
from app.ta.leverage import dynamic_leverage
from app.utils.logger import get_logger
from app.utils.math_utils import position_size, volatility_size_factor

if TYPE_CHECKING:
    from app.ta.signals import SignalResult

logger = get_logger(__name__)

_COOLDOWN_MS      = 10 * 60 * 60 * 1000   # 10 h after a single stop
_WINDOW_24H       = 24 * 60 * 60 * 1000   # rolling 24-h stop window
_WINDOW_8H        =  8 * 60 * 60 * 1000   # 8-h portfolio circuit-breaker window
_PORTFOLIO_MAX_STOPS = 3                   # halt ALL new trades after this many stops in 8 h
_TP_BL_MS         =  4 * 60 * 60 * 1000   # zone blacklist duration after TP
_STOP_BL_MS       = 24 * 60 * 60 * 1000   # zone blacklist duration after stop

# Exit reasons that are market orders in reality — adverse slippage applies
_SLIPPED_EXITS = {"stopped", "timeout", "emergency_rotated"}


def _utc_midnight_ms(now_ms: int | None = None) -> int:
    now = datetime.fromtimestamp((now_ms or time.time() * 1000) / 1000, tz=timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(midnight.timestamp() * 1000)


class PositionManager:
    """
    Manages paper trade lifecycle for one or more symbols.

    Thread-safe: the price monitor and the 30-m signal loop both call
    check_price / check_candle and are protected by an internal lock so
    the same trade cannot be closed twice.
    """

    def __init__(
        self,
        symbols: list[str],
        risk_pct: float = 1.0,
        max_portfolio: int = 3,
        max_same_dir: int = 2,
    ) -> None:
        self.symbols      = [s.upper() for s in symbols]
        self.risk_pct     = risk_pct
        self.max_portfolio = max_portfolio
        self.max_same_dir  = max_same_dir
        self._lock         = threading.Lock()

    # ── Query ─────────────────────────────────────────────────────────────────

    def open_trades(self, symbol: str) -> list[dict]:
        """All open trades for a single symbol."""
        return get_open_paper_trades(symbol)

    def all_open_trades(self) -> list[dict]:
        """
        All open trades across EVERY symbol, DB-wide.

        Deliberately not limited to self.symbols: run_live_paper.py runs one
        process per symbol, so the portfolio and correlation caps must see
        trades opened by sibling processes too.
        """
        return get_all_open_paper_trades()

    def pending_orders(self, symbol: str) -> list[dict]:
        return get_pending_orders(symbol)

    # ── Guards ────────────────────────────────────────────────────────────────

    def can_open(
        self,
        symbol: str,
        direction: str,
        entry: float,
        ignore_pending_id: int | None = None,
    ) -> tuple[bool, str]:
        """
        Run all pre-trade guards in order.
        Returns (allowed, reason_if_blocked).

        ignore_pending_id: when filling a pending order, exclude that order
        from the "slot already reserved" guard so the remaining guards
        (caps, breakers, blacklist) still run.
        """
        cfg = get_settings()

        # 1. Per-symbol: max 1 open trade (a pending order also reserves the slot)
        if self.open_trades(symbol):
            return False, f"Trade already open for {symbol}"
        others = [
            o for o in get_pending_orders(symbol)
            if o["id"] != ignore_pending_id
        ]
        if others:
            return False, f"Pending order already placed for {symbol}"

        # 2. Portfolio cap
        all_open = self.all_open_trades()
        if len(all_open) >= self.max_portfolio:
            return False, (
                f"Portfolio cap: {len(all_open)}/{self.max_portfolio} trades open"
            )

        # 3. Direction (correlation) cap
        same_dir = [t for t in all_open if t.get("direction") == direction]
        if len(same_dir) >= self.max_same_dir:
            syms = ", ".join(t.get("symbol", "?") for t in same_dir)
            return False, (
                f"Correlation cap: already {len(same_dir)} {direction} trades "
                f"open ({syms})"
            )

        # 4. Portfolio open-risk budget
        equity = get_portfolio_capital()
        open_risk = sum(float(t.get("capital_at_risk") or 0) for t in all_open)
        max_risk = equity * (cfg.max_open_risk_pct / 100)
        if open_risk >= max_risk:
            return False, (
                f"Open-risk budget: ${open_risk:,.0f} at risk ≥ "
                f"{cfg.max_open_risk_pct:.1f}% of ${equity:,.0f} equity"
            )

        # 5. Daily loss circuit breaker (realized, portfolio-wide, UTC day)
        daily_pnl = get_closed_pnl_since(_utc_midnight_ms())
        max_daily_loss = equity * (cfg.default_max_daily_loss_pct / 100)
        if daily_pnl <= -max_daily_loss:
            return False, (
                f"Daily loss breaker: {daily_pnl:,.2f} today ≤ "
                f"-{cfg.default_max_daily_loss_pct:.1f}% of equity"
            )

        # 6. Stop-out cooldown (10 h after single stop)
        last_stop = get_last_stopped_trade_ms(symbol)
        now_ms = int(time.time() * 1000)
        if last_stop and (now_ms - last_stop) < _COOLDOWN_MS:
            remaining_min = int((_COOLDOWN_MS - (now_ms - last_stop)) / 60_000)
            return False, f"Stop cooldown active for {symbol}: {remaining_min} min left"

        # 7. Extended block: 2+ stops in last 24 h (per-symbol)
        recent_stops = get_recent_stops_in_window(symbol, _WINDOW_24H)
        if recent_stops >= 2:
            return False, f"{symbol} blocked: {recent_stops} stops in last 24 h"

        # 7b. Per-symbol risk memory: bench a structurally-broken symbol whose
        # stops cluster over a multi-day window even when spaced too far apart to
        # trip the intraday cooldowns (RIFUSDT stopped 3× over 7 days). 0 disables.
        if cfg.symbol_bench_max_stops > 0 and cfg.symbol_bench_window_days > 0:
            bench_window_ms = int(cfg.symbol_bench_window_days * 24 * 60 * 60 * 1000)
            bench_stops, _ = get_symbol_risk_stats(symbol, bench_window_ms)
            if bench_stops >= cfg.symbol_bench_max_stops:
                return False, (
                    f"{symbol} benched: {bench_stops} stops in last "
                    f"{cfg.symbol_bench_window_days:.0f} d "
                    f"(≥{cfg.symbol_bench_max_stops})"
                )

        # 8. Portfolio circuit breaker: 3+ stops across any symbols in last 8 h
        portfolio_stops = get_portfolio_stops_in_window(_WINDOW_8H)
        if portfolio_stops >= _PORTFOLIO_MAX_STOPS:
            return False, (
                f"Portfolio circuit breaker: {portfolio_stops} stops across all symbols "
                f"in last 8 h — halting new entries"
            )

        # 9. Zone blacklist
        if is_zone_blacklisted(symbol, entry):
            return False, f"Zone near {entry:.4f} blacklisted for {symbol}"

        return True, ""

    # ── Submit (limit-or-market) ──────────────────────────────────────────────

    def submit(
        self,
        symbol: str,
        sig: "SignalResult",
        capital: float,
        signal_id: int | None,
        current_price: float | None = None,
    ) -> tuple[str, int | None]:
        """
        Entry point for new signals.

        Returns (outcome, id) where outcome is:
          'opened'  — market entry, id = trade_id
          'pending' — resting limit placed, id = order_id
          'blocked' — guards rejected, id = None
        """
        cfg = get_settings()
        if sig.signal not in ("BUY", "SELL") or sig.entry_price <= 0:
            return "blocked", None

        if not cfg.entry_limit_enabled or current_price is None or current_price <= 0:
            trade_id = self.open(symbol, sig, capital, signal_id)
            return ("opened", trade_id) if trade_id else ("blocked", None)

        # Does price still need to retrace to the entry level?
        gap = cfg.entry_limit_min_gap_pct / 100
        needs_retrace = (
            sig.entry_price < current_price * (1 - gap)
            if sig.signal == "BUY"
            else sig.entry_price > current_price * (1 + gap)
        )
        if not needs_retrace:
            trade_id = self.open(symbol, sig, capital, signal_id)
            return ("opened", trade_id) if trade_id else ("blocked", None)

        allowed, reason = self.can_open(symbol, sig.signal, sig.entry_price)
        if not allowed:
            logger.info("[PM][%s] Pending order blocked: %s", symbol, reason)
            return "blocked", None

        now_ms = int(time.time() * 1000)
        expiry_ms = now_ms + cfg.entry_limit_expiry_candles * 30 * 60 * 1000
        order_id = create_pending_order({
            "symbol":        symbol,
            "signal_id":     signal_id,
            "direction":     sig.signal,
            "limit_price":   round(sig.entry_price, 6),
            "stop_loss":     round(sig.stop_loss, 6),
            "take_profit":   round(sig.take_profit, 6),
            "risk_reward":   round(sig.risk_reward, 2),
            "model_version": sig.model_version,
            "created_ms":    now_ms,
            "expiry_ms":     expiry_ms,
        })
        _save_snapshot(sig, symbol, signal_id)
        logger.info(
            "[PM][%s] Pending %s limit @ %.6f (now %.6f), expires in %d candles",
            symbol, sig.signal, sig.entry_price, current_price,
            cfg.entry_limit_expiry_candles,
        )
        return "pending", order_id

    def check_pending(self, symbol: str, high: float, low: float) -> list[dict]:
        """
        Fill or expire pending orders against a price range (candle or tick).
        Returns a list of events: {"order": ..., "outcome": "filled|expired|cancelled",
        "trade_id": int | None}.
        """
        events: list[dict] = []
        now_ms = int(time.time() * 1000)

        with self._lock:
            for order in get_pending_orders(symbol):
                if now_ms >= int(order["expiry_ms"]):
                    update_pending_order_status(order["id"], "expired")
                    logger.info("[PM][%s] Pending order #%d expired", symbol, order["id"])
                    events.append({"order": order, "outcome": "expired", "trade_id": None})
                    continue

                limit = float(order["limit_price"])
                touched = high >= limit if order["direction"] == "SELL" else low <= limit
                if not touched:
                    continue

                # Re-run guards at fill time — cooldowns/caps may have changed
                allowed, reason = self.can_open(
                    symbol, order["direction"], limit,
                    ignore_pending_id=order["id"],
                )
                if not allowed:
                    update_pending_order_status(order["id"], "cancelled")
                    logger.info(
                        "[PM][%s] Pending order #%d cancelled at fill: %s",
                        symbol, order["id"], reason,
                    )
                    events.append({"order": order, "outcome": "cancelled", "trade_id": None})
                    continue

                trade_id = self._open_at(
                    symbol      = symbol,
                    direction   = order["direction"],
                    entry       = limit,           # limit fills at the limit price
                    stop_loss   = float(order["stop_loss"]),
                    take_profit = float(order["take_profit"]),
                    rr          = float(order["risk_reward"] or 0),
                    signal_id   = order["signal_id"],
                    model_version = order.get("model_version") or "rule_based_v1",
                )
                update_pending_order_status(
                    order["id"], "filled" if trade_id else "cancelled", trade_id
                )
                events.append({
                    "order": order,
                    "outcome": "filled" if trade_id else "cancelled",
                    "trade_id": trade_id,
                })

        return events

    # ── Open ──────────────────────────────────────────────────────────────────

    def open(
        self,
        symbol: str,
        sig: "SignalResult",
        capital: float,
        signal_id: int | None,
    ) -> int | None:
        """
        Market entry: apply fill slippage, run guards, insert trade row.
        Returns trade_id or None if blocked.
        """
        if sig.signal not in ("BUY", "SELL"):
            return None

        # Simulated market fill (slippage)
        filled_entry = simulate_fill(sig.signal, sig.entry_price)
        sig = dataclasses.replace(sig, entry_price=round(filled_entry, 6))

        if sig.entry_price <= 0 or sig.stop_loss <= 0:
            return None

        allowed, reason = self.can_open(symbol, sig.signal, sig.entry_price)
        if not allowed:
            logger.info("[PM][%s] Trade blocked: %s", symbol, reason)
            return None

        trade_id = self._open_at(
            symbol      = symbol,
            direction   = sig.signal,
            entry       = sig.entry_price,
            stop_loss   = sig.stop_loss,
            take_profit = sig.take_profit,
            rr          = sig.risk_reward,
            signal_id   = signal_id,
            model_version = sig.model_version,
            daily_atr_pct = sig.daily_atr_pct,
        )
        if trade_id:
            _save_snapshot(sig, symbol, signal_id)
        return trade_id

    def _open_at(
        self,
        symbol: str,
        direction: str,
        entry: float,
        stop_loss: float,
        take_profit: float,
        rr: float,
        signal_id: int | None,
        model_version: str,
        daily_atr_pct: float = 0.0,
    ) -> int | None:
        """Shared insert path for market entries and limit fills."""
        cfg = get_settings()

        recent_trades = get_recent_closed_paper_trades(symbol, n=5)
        lev = dynamic_leverage(
            _LeverageSig(direction, rr),
            recent_trades=recent_trades,
            max_leverage=cfg.max_leverage,
        )

        # Risk scales down for each additional same-direction position —
        # concurrent crypto positions are highly correlated.
        same_dir = [
            t for t in self.all_open_trades() if t.get("direction") == direction
        ]
        scale_idx = min(len(same_dir), len(cfg.corr_risk_scale) - 1)
        eff_risk_pct = self.risk_pct * cfg.corr_risk_scale[scale_idx]

        # Volatility-scaled sizing: shrink risk on high daily-ATR% coins instead
        # of vetoing them (they keep positive expectancy but a fat loss tail).
        vol_factor = volatility_size_factor(
            daily_atr_pct,
            full_atr_pct=cfg.vol_size_full_atr_pct,
            slope=cfg.vol_size_slope,
            floor=cfg.vol_size_floor,
        )
        if vol_factor < 1.0:
            logger.info(
                "[PM][%s] Volatility sizing: daily ATR %.1f%% → risk ×%.2f",
                symbol, daily_atr_pct, vol_factor,
            )
        eff_risk_pct *= vol_factor

        # Size off the unified portfolio equity, not a per-symbol silo
        equity = get_portfolio_capital()
        pos_size = position_size(equity, eff_risk_pct, entry, stop_loss, leverage=lev)
        if pos_size <= 0:
            return None

        trade_id = open_paper_trade({
            "symbol":          symbol,
            "signal_id":       signal_id,
            "direction":       direction,
            "entry_price":     round(entry, 6),
            "stop_loss":       round(stop_loss, 6),
            "take_profit":     round(take_profit, 6),
            "position_size":   round(pos_size, 6),
            "capital_at_risk": round(equity * (eff_risk_pct / 100) * lev, 2),
            "risk_reward":     round(rr, 2),
            "open_time":       int(time.time() * 1000),
            "model_version":   model_version,
            "original_risk":   round(abs(entry - stop_loss), 6),
            "leverage":        lev,
        })

        if trade_id:
            logger.info(
                "[PM][%s] Opened %s @ %.6f  SL=%.6f  TP=%.6f  lev=%s×  risk=%.2f%%",
                symbol, direction, entry, stop_loss, take_profit, lev, eff_risk_pct,
            )
        return trade_id

    # ── Close ─────────────────────────────────────────────────────────────────

    def close(
        self,
        trade: dict,
        close_price: float,
        reason: str,
        timestamp: int | None = None,
    ) -> dict:
        """
        Close a trade atomically.

        reason: 'target_hit' | 'stopped' | 'timeout' | 'closed' | 'emergency_rotated'
        Always writes close_time; never leaves it null or epoch-2.

        The returned dict carries:
          pnl           — TOTAL trade PnL (incl. any partial TP), stored in DB
          pnl_increment — PnL realized by THIS close only; callers must apply
                          this (not pnl) to capital, since partial TP already
                          updated capital when it was taken.
        """
        cfg          = get_settings()
        fee_pct      = cfg.backtest_fee_pct / 100
        slippage_pct = cfg.backtest_slippage_pct / 100

        close_ts  = timestamp or int(time.time() * 1000)
        symbol    = trade["symbol"]
        direction = trade["direction"]
        entry     = float(trade["entry_price"])
        pos_size  = float(trade["position_size"])
        equity    = get_portfolio_capital()

        # Stop-type exits are market orders in reality — adverse slippage
        if reason in _SLIPPED_EXITS:
            close_price = (
                close_price * (1 - slippage_pct)
                if direction == "BUY"
                else close_price * (1 + slippage_pct)
            )

        raw_pnl = (
            (close_price - entry) * pos_size
            if direction == "BUY"
            else (entry - close_price) * pos_size
        )
        fee = entry * pos_size * fee_pct * 2
        pnl_increment = raw_pnl - fee
        partial_pnl   = float(trade.get("partial_pnl") or 0)
        pnl_total     = pnl_increment + partial_pnl
        pnl_pct = pnl_total / equity * 100 if equity > 0 else 0.0

        mfe = float(trade.get("max_favorable_excursion") or 0)
        mae = float(trade.get("max_adverse_excursion") or 0)

        close_paper_trade(
            trade_id    = trade["id"],
            close_price = round(close_price, 6),
            close_time  = close_ts,
            pnl         = round(pnl_total, 2),
            pnl_pct     = round(pnl_pct, 4),
            status      = reason,
            mfe         = round(mfe, 6),
            mae         = round(mae, 6),
        )

        # Zone blacklist prevents re-entry on the same level
        if reason == "stopped":
            add_zone_blacklist(symbol, entry, duration_ms=_STOP_BL_MS)
        elif reason == "target_hit":
            add_zone_blacklist(symbol, entry, duration_ms=_TP_BL_MS)

        _update_snapshot_outcome(trade["id"], "win" if pnl_total > 0 else "loss", pnl_total)

        logger.info(
            "[PM][%s] Closed %s %s @ %.6f  pnl=%.2f (%.2f%%)%s",
            symbol, direction, reason, close_price, pnl_total, pnl_pct,
            f"  (incl. partial {partial_pnl:+.2f})" if partial_pnl else "",
        )

        return {
            **trade,
            "close_price":   round(close_price, 6),
            "close_time":    close_ts,
            "pnl":           round(pnl_total, 2),
            "pnl_increment": round(pnl_increment, 2),
            "pnl_pct":       round(pnl_pct, 4),
            "status":        reason,
        }

    # ── Price checks ──────────────────────────────────────────────────────────

    def check_price(self, symbol: str, price: float) -> list[dict]:
        """
        Check open trades against a single price point (e.g. a tick or
        an intraday poll).  Equivalent to check_candle with high=low=close.

        No ATR is available for a bare price tick, so the ratchet trailing
        stop does not advance here — it only advances on check_candle(),
        which runs once per 30-m candle close with a real ATR reading.
        """
        closed, _ratcheted = self._evaluate(symbol, high=price, low=price, close=price)
        return closed

    def check_candle(
        self,
        symbol: str,
        high: float,
        low: float,
        close: float,
        atr: float | None = None,
    ) -> tuple[list[dict], list[dict]]:
        """
        Check open trades against a completed candle.
        Uses high/low for SL/TP detection (catches wicks) and close for
        MFE/MAE.  If a candle trips both SL and TP, the stop takes priority —
        intrabar order is unknown from H/L alone, so be conservative.

        `atr` (14-period ATR of the same candle) drives the ratchet trailing
        stop; pass it to advance the stop toward breakeven/locked-profit/ATR
        trail as the trade moves through +1R/+2R/+3R/+4R. Returns
        (closed_trades, ratchet_events) — ratchet_events is a list of
        {"trade", "new_sl", "new_level"} dicts, one per trade whose stop
        tightened to a new level this cycle.
        """
        return self._evaluate(symbol, high=high, low=low, close=close, atr=atr)

    def _evaluate(
        self,
        symbol: str,
        high: float,
        low: float,
        close: float,
        atr: float | None = None,
    ) -> tuple[list[dict], list[dict]]:
        """
        Core evaluation loop — thread-safe via lock so the price monitor
        and the 30-m signal thread cannot double-close the same trade.
        """
        cfg = get_settings()
        fee_pct = cfg.backtest_fee_pct / 100
        max_age_ms = int(cfg.max_trade_age_hours * 3_600_000)
        closed: list[dict] = []
        ratcheted: list[dict] = []

        with self._lock:
            open_trades = get_open_paper_trades(symbol)
            equity      = get_portfolio_capital()
            now_ms      = int(time.time() * 1000)

            for trade in open_trades:
                direction = trade["direction"]
                entry     = float(trade["entry_price"])
                sl        = float(trade["stop_loss"])
                tp        = float(trade["take_profit"])
                pos_size  = float(trade["position_size"])
                risk0     = float(trade.get("original_risk") or 0) or abs(entry - sl)

                # Ratchet trailing stop: +1R->BE, +2R->lock 0.75R, +3R->lock
                # 1.5R, +4R+->ATR trail. Only advances on candle closes (atr
                # is None on bare ticks from check_price). Stop only ever
                # tightens, never loosens — see app/ta/exit_engine.py.
                if cfg.mtf_ratchet_enabled and atr is not None and risk0 > 0:
                    ratchet_levels = build_ratchet_levels(entry, sl, direction)
                    old_level = int(trade.get("ratchet_level") or 0)
                    new_sl, new_level = apply_ratchet(
                        direction=direction,
                        original_risk=risk0,
                        current_sl=sl,
                        candle_high=high,
                        candle_low=low,
                        atr=atr,
                        ratchet_level=old_level,
                        ratchet_levels=ratchet_levels,
                        entry=entry,
                    )
                    if new_sl != sl or new_level != old_level:
                        update_paper_trade_stop(trade["id"], new_sl, new_level)
                        sl = new_sl
                        trade = {**trade, "stop_loss": new_sl, "ratchet_level": new_level}
                        if new_level > old_level:
                            ratcheted.append(
                                {"trade": trade, "new_sl": new_sl, "new_level": new_level}
                            )
                            logger.info(
                                "[PM][%s] Ratchet %s: level %d -> SL %.6f",
                                symbol, direction, new_level, new_sl,
                            )

                # Intrabar SL/TP using candle extremes
                if direction == "BUY":
                    hit_sl = low  <= sl
                    hit_tp = high >= tp
                else:
                    hit_sl = high >= sl
                    hit_tp = low  <= tp

                # Partial take-profit at +partial_tp_r R. Skipped when the stop
                # (conservative: assume it came first) or the full TP (the full
                # exit supersedes) also sits inside this range.
                if (
                    cfg.partial_tp_enabled
                    and not trade.get("partial_taken")
                    and not hit_sl
                    and not hit_tp
                    and risk0 > 0
                ):
                    target = (
                        entry + cfg.partial_tp_r * risk0
                        if direction == "BUY"
                        else entry - cfg.partial_tp_r * risk0
                    )
                    reached = high >= target if direction == "BUY" else low <= target
                    if reached:
                        closed_size = pos_size * cfg.partial_tp_fraction
                        raw = (
                            (target - entry) * closed_size
                            if direction == "BUY"
                            else (entry - target) * closed_size
                        )
                        ppnl = raw - entry * closed_size * fee_pct * 2
                        pos_size -= closed_size
                        apply_partial_tp(trade["id"], pos_size, ppnl)
                        update_capital_after_trade(symbol, ppnl)
                        trade = {
                            **trade,
                            "position_size": pos_size,
                            "partial_taken": 1,
                            "partial_pnl":   round(ppnl, 2),
                        }
                        logger.info(
                            "[PM][%s] Partial TP %s: closed %.0f%% @ %.6f  pnl=%.2f",
                            symbol, direction, cfg.partial_tp_fraction * 100,
                            target, ppnl,
                        )

                # Running MFE/MAE via close price
                stored_mfe = float(trade.get("max_favorable_excursion") or 0)
                stored_mae = float(trade.get("max_adverse_excursion") or 0)
                if direction == "BUY":
                    curr_mfe = max(0.0, close - entry)
                    curr_mae = max(0.0, entry - close)
                else:
                    curr_mfe = max(0.0, entry - close)
                    curr_mae = max(0.0, close - entry)
                new_mfe = max(stored_mfe, curr_mfe)
                new_mae = max(stored_mae, curr_mae)

                # Stagnation timeout — live parity with the backtest engine
                timed_out = (
                    not hit_sl and not hit_tp
                    and now_ms - int(trade["open_time"]) >= max_age_ms
                )

                if not (hit_sl or hit_tp or timed_out):
                    unrealized = (
                        (close - entry) * pos_size
                        if direction == "BUY"
                        else (entry - close) * pos_size
                    )
                    unrealized += float(trade.get("partial_pnl") or 0)
                    unrealized_pct = unrealized / equity * 100 if equity > 0 else 0.0
                    update_open_paper_trade_pnl(
                        trade["id"],
                        round(unrealized, 2),
                        round(unrealized_pct, 4),
                        round(new_mfe, 6),
                        round(new_mae, 6),
                    )
                    continue

                # Stop takes priority on candles that hit both levels —
                # intrabar order is unknown, assume the worse outcome
                if hit_sl:
                    exit_price = sl
                    reason     = "stopped"
                    final_mfe  = new_mfe
                    final_mae  = max(new_mae, abs(sl - entry))
                elif hit_tp:
                    exit_price = tp
                    reason     = "target_hit"
                    final_mfe  = max(new_mfe, abs(tp - entry))
                    final_mae  = new_mae
                else:  # timeout — exit at current price
                    exit_price = close
                    reason     = "timeout"
                    final_mfe  = new_mfe
                    final_mae  = new_mae

                trade_with_excursions = {
                    **trade,
                    "max_favorable_excursion": round(final_mfe, 6),
                    "max_adverse_excursion":   round(final_mae, 6),
                }

                result = self.close(trade_with_excursions, exit_price, reason)
                update_capital_after_trade(symbol, result["pnl_increment"])
                closed.append(result)

        return closed, ratcheted

    # ── Emergency close ───────────────────────────────────────────────────────

    def emergency_close(self, symbol: str, regime: str) -> list[dict]:
        """
        Force-close all open trades for a symbol at the current market price.
        Used when a dangerous market regime is detected during rescan.
        """
        open_trades = self.open_trades(symbol)
        if not open_trades:
            return []

        # Prefer live ticker; fall back to last candle close
        try:
            from app.data.binance_client import BinanceClient
            with BinanceClient() as client:
                last_price = client.get_ticker_price(symbol)
        except Exception:
            from app.data.repository import get_candles
            df = get_candles(symbol, "30m", limit=1)
            if df.empty:
                logger.warning("[PM][%s] Emergency close: no price data", symbol)
                return []
            last_price = float(df["close"].iloc[-1])

        closed = []
        for trade in open_trades:
            result = self.close(trade, last_price, "emergency_rotated")
            update_capital_after_trade(symbol, result["pnl_increment"])
            closed.append(result)
            logger.info(
                "[PM][%s] Emergency close %s @ %.6f  pnl=%.2f  regime=%s",
                symbol, trade["direction"], last_price, result["pnl"], regime,
            )

        return closed


# ── Private helpers ────────────────────────────────────────────────────────────

class _LeverageSig:
    """Minimal stand-in for SignalResult when sizing a limit fill."""
    def __init__(self, signal: str, risk_reward: float) -> None:
        self.signal       = signal
        self.risk_reward  = risk_reward
        self.confidence   = 0.0
        self.market_regime = ""
        self.zone_score   = 0.0


def _save_snapshot(sig: "SignalResult", symbol: str, signal_id: int | None) -> None:
    try:
        save_feature_snapshot({
            "signal_id": signal_id,
            "symbol":    symbol,
            "timestamp": int(time.time() * 1000),
            "features": {
                "signal":           sig.signal,
                "h4_bias":          sig.h4_bias,
                "h1_confirmation":  sig.h1_confirmation,
                "zone_score":       sig.zone_score,
                "liquidity_sweep":  int(sig.liquidity_sweep),
                "premium_discount": sig.premium_discount,
                "risk_reward":      sig.risk_reward,
                "market_regime":    sig.market_regime,
                "confidence":       sig.confidence,
            },
            "outcome": None,
            "pnl":     None,
        })
    except Exception as exc:
        logger.warning("[PM] Could not save feature snapshot: %s", exc)


def _update_snapshot_outcome(trade_id: int, outcome: str, pnl: float) -> None:
    try:
        from app.db.connection import get_conn
        with get_conn() as conn:
            conn.execute(
                """UPDATE feature_snapshots SET outcome=?, pnl=?
                   WHERE signal_id = (
                       SELECT signal_id FROM paper_trades WHERE id=?
                   )""",
                (outcome, round(pnl, 2), trade_id),
            )
    except Exception as exc:
        logger.warning("[PM] Could not update snapshot outcome for trade %d: %s", trade_id, exc)
