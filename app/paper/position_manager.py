"""
PositionManager — single owner of all live/paper trade lifecycle.

All state transitions (open → stopped / target_hit / closed) go through
this class. Nothing else writes paper_trades directly in live mode.
"""

from __future__ import annotations

import dataclasses
import threading
import time
from typing import TYPE_CHECKING

from app.config import get_settings
from app.data.repository import (
    add_zone_blacklist,
    close_paper_trade,
    get_last_stopped_trade_ms,
    get_open_paper_trades,
    get_portfolio_stops_in_window,
    get_recent_closed_paper_trades,
    get_recent_stops_in_window,
    is_zone_blacklisted,
    open_paper_trade,
    save_feature_snapshot,
    update_open_paper_trade_pnl,
)
from app.paper.account import get_paper_capital, update_capital_after_trade
from app.paper.broker import simulate_fill
from app.ta.leverage import dynamic_leverage
from app.utils.logger import get_logger
from app.utils.math_utils import position_size

if TYPE_CHECKING:
    from app.ta.signals import SignalResult

logger = get_logger(__name__)

_COOLDOWN_MS      = 10 * 60 * 60 * 1000   # 10 h after a single stop
_WINDOW_24H       = 24 * 60 * 60 * 1000   # rolling 24-h stop window
_WINDOW_8H        =  8 * 60 * 60 * 1000   # 8-h portfolio circuit-breaker window
_PORTFOLIO_MAX_STOPS = 3                   # halt ALL new trades after this many stops in 8 h
_TP_BL_MS         =  4 * 60 * 60 * 1000   # zone blacklist duration after TP
_STOP_BL_MS       = 24 * 60 * 60 * 1000   # zone blacklist duration after stop


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
        """All open trades across every tracked symbol."""
        trades: list[dict] = []
        for sym in self.symbols:
            for t in get_open_paper_trades(sym):
                t = dict(t)
                t.setdefault("symbol", sym)
                trades.append(t)
        return trades

    # ── Guards ────────────────────────────────────────────────────────────────

    def can_open(
        self,
        symbol: str,
        direction: str,
        entry: float,
    ) -> tuple[bool, str]:
        """
        Run all pre-trade guards in order.
        Returns (allowed, reason_if_blocked).
        """
        # 1. Per-symbol: max 1 open trade
        if self.open_trades(symbol):
            return False, f"Trade already open for {symbol}"

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

        # 4. Stop-out cooldown (10 h after single stop)
        last_stop = get_last_stopped_trade_ms(symbol)
        now_ms = int(time.time() * 1000)
        if last_stop and (now_ms - last_stop) < _COOLDOWN_MS:
            remaining_min = int((_COOLDOWN_MS - (now_ms - last_stop)) / 60_000)
            return False, f"Stop cooldown active for {symbol}: {remaining_min} min left"

        # 5. Extended block: 2+ stops in last 24 h (per-symbol)
        recent_stops = get_recent_stops_in_window(symbol, _WINDOW_24H)
        if recent_stops >= 2:
            return False, f"{symbol} blocked: {recent_stops} stops in last 24 h"

        # 6. Portfolio circuit breaker: 3+ stops across any symbols in last 8 h
        portfolio_stops = get_portfolio_stops_in_window(_WINDOW_8H)
        if portfolio_stops >= _PORTFOLIO_MAX_STOPS:
            return False, (
                f"Portfolio circuit breaker: {portfolio_stops} stops across all symbols "
                f"in last 8 h — halting new entries"
            )

        # 7. Zone blacklist
        if is_zone_blacklisted(symbol, entry):
            return False, f"Zone near {entry:.4f} blacklisted for {symbol}"

        return True, ""

    # ── Open ──────────────────────────────────────────────────────────────────

    def open(
        self,
        symbol: str,
        sig: "SignalResult",
        capital: float,
        signal_id: int | None,
    ) -> int | None:
        """
        Apply fill slippage, run guards, insert trade row.
        Returns trade_id or None if blocked.
        """
        if sig.signal not in ("BUY", "SELL"):
            return None

        cfg = get_settings()

        # Simulated fill (slippage)
        filled_entry = simulate_fill(sig.signal, sig.entry_price)
        sig = dataclasses.replace(sig, entry_price=round(filled_entry, 6))

        if sig.entry_price <= 0 or sig.stop_loss <= 0:
            return None

        allowed, reason = self.can_open(symbol, sig.signal, sig.entry_price)
        if not allowed:
            logger.info("[PM][%s] Trade blocked: %s", symbol, reason)
            return None

        recent_trades = get_recent_closed_paper_trades(symbol, n=5)
        lev = dynamic_leverage(
            sig,
            recent_trades=recent_trades,
            max_leverage=cfg.max_leverage,
        )
        pos_size = position_size(
            capital, self.risk_pct, sig.entry_price, sig.stop_loss, leverage=lev
        )
        if pos_size <= 0:
            return None

        trade_id = open_paper_trade({
            "symbol":          symbol,
            "signal_id":       signal_id,
            "direction":       sig.signal,
            "entry_price":     round(sig.entry_price, 6),
            "stop_loss":       round(sig.stop_loss, 6),
            "take_profit":     round(sig.take_profit, 6),
            "position_size":   round(pos_size, 6),
            "capital_at_risk": round(capital * (self.risk_pct / 100) * lev, 2),
            "risk_reward":     round(sig.risk_reward, 2),
            "open_time":       int(time.time() * 1000),
            "model_version":   sig.model_version,
            "original_risk":   round(abs(sig.entry_price - sig.stop_loss), 6),
            "leverage":        lev,
        })

        if trade_id:
            _save_snapshot(sig, symbol, signal_id)
            logger.info(
                "[PM][%s] Opened %s @ %.6f  SL=%.6f  TP=%.6f  lev=%s×",
                symbol, sig.signal, sig.entry_price,
                sig.stop_loss, sig.take_profit, lev,
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

        reason: 'target_hit' | 'stopped' | 'closed' | 'emergency_rotated'
        Always writes close_time; never leaves it null or epoch-2.
        """
        cfg     = get_settings()
        fee_pct = cfg.backtest_fee_pct / 100

        close_ts  = timestamp or int(time.time() * 1000)
        symbol    = trade["symbol"]
        direction = trade["direction"]
        entry     = float(trade["entry_price"])
        pos_size  = float(trade["position_size"])
        capital   = get_paper_capital(symbol)

        raw_pnl = (
            (close_price - entry) * pos_size
            if direction == "BUY"
            else (entry - close_price) * pos_size
        )
        fee = entry * pos_size * fee_pct * 2
        pnl = raw_pnl - fee
        pnl_pct = pnl / capital * 100 if capital > 0 else 0.0

        mfe = float(trade.get("max_favorable_excursion") or 0)
        mae = float(trade.get("max_adverse_excursion") or 0)

        close_paper_trade(
            trade_id    = trade["id"],
            close_price = round(close_price, 6),
            close_time  = close_ts,
            pnl         = round(pnl, 2),
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

        _update_snapshot_outcome(trade["id"], "win" if pnl > 0 else "loss", pnl)

        logger.info(
            "[PM][%s] Closed %s %s @ %.6f  pnl=%.2f (%.2f%%)",
            symbol, direction, reason, close_price, pnl, pnl_pct,
        )

        return {
            **trade,
            "close_price": round(close_price, 6),
            "close_time":  close_ts,
            "pnl":         round(pnl, 2),
            "pnl_pct":     round(pnl_pct, 4),
            "status":      reason,
        }

    # ── Price checks ──────────────────────────────────────────────────────────

    def check_price(self, symbol: str, price: float) -> list[dict]:
        """
        Check open trades against a single price point (e.g. a tick or
        an intraday poll).  Equivalent to check_candle with high=low=close.
        """
        return self._evaluate(symbol, high=price, low=price, close=price)

    def check_candle(
        self,
        symbol: str,
        high: float,
        low: float,
        close: float,
    ) -> list[dict]:
        """
        Check open trades against a completed candle.
        Uses high/low for SL/TP detection (catches wicks) and close for
        MFE/MAE.  If a gap candle trips both SL and TP, TP takes priority.
        """
        return self._evaluate(symbol, high=high, low=low, close=close)

    def _evaluate(
        self,
        symbol: str,
        high: float,
        low: float,
        close: float,
    ) -> list[dict]:
        """
        Core evaluation loop — thread-safe via lock so the price monitor
        and the 30-m signal thread cannot double-close the same trade.
        """
        closed: list[dict] = []

        with self._lock:
            open_trades = get_open_paper_trades(symbol)
            capital     = get_paper_capital(symbol)

            for trade in open_trades:
                direction = trade["direction"]
                entry     = float(trade["entry_price"])
                sl        = float(trade["stop_loss"])
                tp        = float(trade["take_profit"])
                pos_size  = float(trade["position_size"])

                # Intrabar SL/TP using candle extremes
                if direction == "BUY":
                    hit_sl = low  <= sl
                    hit_tp = high >= tp
                else:
                    hit_sl = high >= sl
                    hit_tp = low  <= tp

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

                if not (hit_sl or hit_tp):
                    unrealized = (
                        (close - entry) * pos_size
                        if direction == "BUY"
                        else (entry - close) * pos_size
                    )
                    unrealized_pct = unrealized / capital * 100 if capital > 0 else 0.0
                    update_open_paper_trade_pnl(
                        trade["id"],
                        round(unrealized, 2),
                        round(unrealized_pct, 4),
                        round(new_mfe, 6),
                        round(new_mae, 6),
                    )
                    continue

                # TP takes priority on gap candles that hit both levels
                if hit_tp:
                    exit_price = tp
                    reason     = "target_hit"
                    final_mfe  = max(new_mfe, abs(tp - entry))
                    final_mae  = new_mae
                else:
                    exit_price = sl
                    reason     = "stopped"
                    final_mfe  = new_mfe
                    final_mae  = max(new_mae, abs(sl - entry))

                trade_with_excursions = {
                    **trade,
                    "max_favorable_excursion": round(final_mfe, 6),
                    "max_adverse_excursion":   round(final_mae, 6),
                }

                result = self.close(trade_with_excursions, exit_price, reason)
                update_capital_after_trade(symbol, result["pnl"])
                closed.append(result)

        return closed

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
            update_capital_after_trade(symbol, result["pnl"])
            closed.append(result)
            logger.info(
                "[PM][%s] Emergency close %s @ %.6f  pnl=%.2f  regime=%s",
                symbol, trade["direction"], last_price, result["pnl"], regime,
            )

        return closed


# ── Private helpers ────────────────────────────────────────────────────────────

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
