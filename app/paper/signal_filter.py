"""
SignalFilter ??? ordered pre-trade filter pipeline.

A SignalResult only becomes a trade if it passes every filter.
Each filter returns (pass: bool, reason: str).  The first failure
short-circuits; the reason is logged and shown in the watcher output.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from app.config import get_settings, get_settings_for_symbol, is_simplified_strategy
from app.utils.logger import get_logger

if TYPE_CHECKING:
    from app.ta.signals import SignalResult

logger = get_logger(__name__)

_BULLISH = {"bullish", "strongly_bullish"}
_BEARISH = {"bearish", "strongly_bearish"}
_BAD_RATINGS = {"C", "ignore", ""}

# PD zones where SELL signals are structurally wrong (buying territory)
_SELL_BLOCKED_PD = {"discount", "deep_discount"}
# PD zones where BUY signals are structurally wrong (selling territory)
_BUY_BLOCKED_PD  = {"premium", "deep_premium"}

# Market regimes that are choppy / no-edge ??? skip entries
_NO_TRADE_REGIMES = {"distribution"}

# UTC hours (0-23) where historical win rate is 0% ??? skip entries
# Based on observed losses: Asia dead zone (01-04) and London/NY overlap chop (13, 15, 23)
_BLOCKED_HOURS_UTC = {1, 3, 4, 13, 15, 23}

# Minimum candle body ratio to call it a "strong momentum candle against the signal"
_MOMENTUM_BODY_THRESHOLD = 0.60
# Minimum wick ratio to call it a rejection candle in the signal direction
_REJECTION_WICK_THRESHOLD = 0.35


class SignalFilter:
    """
    Evaluate a SignalResult against a sequence of quality gates before
    allowing a trade to be opened.

    All thresholds are read from app/config.py so they can be tuned via
    environment variables without touching code.
    """

    def __init__(self) -> None:
        # Optional evaluation context (set per evaluate() call):
        #   _now_ms  ??? signal timestamp for time-of-day checks (backtests pass
        #              the candle time; live defaults to wall clock)
        #   _df_30m  ??? historical 30m frame for candle checks (backtests pass
        #              the lookahead-safe slice; live falls back to the DB)
        self._now_ms: int | None = None
        self._df_30m = None

    def evaluate(
        self,
        sig: "SignalResult",
        *,
        now_ms: int | None = None,
        df_30m=None,
    ) -> tuple[bool, str]:
        """
        Run every filter in order.
        Returns (True, '') if all pass, or (False, reason) on first failure.
        """
        self._now_ms = now_ms
        self._df_30m = df_30m
        cfg = get_settings()
        checks = (
            self._evaluate_simple_checks
            if is_simplified_strategy()
            else self._evaluate_full_checks
        )
        return checks(sig)

    def _evaluate_full_checks(self, sig: "SignalResult") -> tuple[bool, str]:
        for check in (
            self._null_confidence,
            self._zone_rating,
            self._zone_score,
            self._daily_bias,
            self._confidence,
            self._risk_reward,
            self._premium_discount_alignment,
            self._distribution_regime,
            self._trap_zone,
            self._time_of_day,
            self._candle_rejection,
            self._macro_events,
            self._funding_rate,
        ):
            ok, reason = check(sig)
            if not ok:
                logger.info(
                    "[Filter][%s] %s blocked ??? %s",
                    sig.symbol, sig.signal, reason,
                )
                return False, reason
        return True, ""

    def _evaluate_simple_checks(self, sig: "SignalResult") -> tuple[bool, str]:
        """Option B: A/A+ zone, R:R >= 2.0, rejection candle wick >= 35%."""
        for check in (
            self._simple_zone_rating,
            self._simple_risk_reward,
            self._candle_rejection_required,
        ):
            ok, reason = check(sig)
            if not ok:
                logger.info(
                    "[Filter][%s] %s blocked ??? %s",
                    sig.symbol, sig.signal, reason,
                )
                return False, reason
        return True, ""

    # ?????? Existing filters ??????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????

    def _null_confidence(self, sig: "SignalResult") -> tuple[bool, str]:
        """Reject signals with no confidence data (missing or zero ??? bad signal origin)."""
        if not sig.confidence:
            return False, "Confidence is zero or missing ??? signal data incomplete"
        return True, ""

    def _zone_rating(self, sig: "SignalResult") -> tuple[bool, str]:
        """Reject C-rated and unrated zones ??? insufficient confluence."""
        if sig.zone_rating in _BAD_RATINGS:
            return False, f"Zone rating '{sig.zone_rating}' below minimum (need A/A+/B)"
        return True, ""

    def _zone_score(self, sig: "SignalResult") -> tuple[bool, str]:
        """Reject if zone score is below the configured minimum."""
        cfg = get_settings_for_symbol(sig.symbol)
        if sig.zone_score < cfg.min_zone_score:
            return False, (
                f"Zone score {sig.zone_score:.1f} < min {cfg.min_zone_score:.1f}"
            )
        return True, ""

    def _daily_bias(self, sig: "SignalResult") -> tuple[bool, str]:
        """
        Daily bias must agree with the trade direction.
        Neutral daily bias = no trade (insufficient conviction on the
        dominant timeframe).
        """
        bias = (sig.daily_bias or "").lower()
        if sig.signal == "BUY" and bias not in _BULLISH:
            return False, f"Daily bias '{bias}' not bullish for BUY"
        if sig.signal == "SELL" and bias not in _BEARISH:
            return False, f"Daily bias '{bias}' not bearish for SELL"
        return True, ""

    def _confidence(self, sig: "SignalResult") -> tuple[bool, str]:
        """Reject if ML/rule confidence is below the configured minimum."""
        cfg = get_settings_for_symbol(sig.symbol)
        if sig.confidence < cfg.min_signal_confidence:
            return False, (
                f"Confidence {sig.confidence:.1f} < min {cfg.min_signal_confidence:.1f}"
            )
        return True, ""

    def _risk_reward(self, sig: "SignalResult") -> tuple[bool, str]:
        """Reject if the R:R ratio is below the configured minimum."""
        cfg = get_settings()
        if sig.risk_reward < cfg.min_rr_ratio:
            return False, (
                f"R:R {sig.risk_reward:.2f} < min {cfg.min_rr_ratio:.2f}"
            )
        return True, ""

    # ?????? Simplified (v6) filters ?????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????

    def _simple_zone_rating(self, sig: "SignalResult") -> tuple[bool, str]:
        if sig.zone_rating not in ("A", "A+"):
            return False, f"Zone rating '{sig.zone_rating}' below minimum (need A/A+)"
        return True, ""

    def _simple_risk_reward(self, sig: "SignalResult") -> tuple[bool, str]:
        cfg = get_settings()
        floor = cfg.simple_min_rr_ratio
        if sig.risk_reward < floor:
            return False, f"R:R {sig.risk_reward:.2f} < min {floor:.2f}"
        return True, ""

    def _candle_rejection_required(self, sig: "SignalResult") -> tuple[bool, str]:
        """Require rejection wick >= 35% in the signal direction on last closed 30m candle."""
        try:
            if self._df_30m is not None:
                df = self._df_30m
                if len(df) < 1:
                    return False, "No 30m candle data for rejection check"
                c = df.iloc[-1]
            else:
                from app.data.repository import get_candles
                df = get_candles(sig.symbol, "30m", limit=3)
                if df is None or len(df) < 2:
                    return False, "No 30m candle data for rejection check"
                c = df.iloc[-2]
            o, h, l, cl = float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"])
            candle_range = h - l
            if candle_range == 0:
                return False, "Flat candle ??? no rejection wick"
            upper_wick_pct = (h - max(o, cl)) / candle_range
            lower_wick_pct = (min(o, cl) - l) / candle_range
            if sig.signal == "BUY" and lower_wick_pct + 1e-9 < _REJECTION_WICK_THRESHOLD:
                return False, (
                    f"No BUY rejection ??? lower wick {lower_wick_pct:.0%} "
                    f"(need ???{_REJECTION_WICK_THRESHOLD:.0%})"
                )
            if sig.signal == "SELL" and upper_wick_pct + 1e-9 < _REJECTION_WICK_THRESHOLD:
                return False, (
                    f"No SELL rejection ??? upper wick {upper_wick_pct:.0%} "
                    f"(need ???{_REJECTION_WICK_THRESHOLD:.0%})"
                )
        except Exception as exc:
            logger.warning("[Filter] candle_rejection_required failed: %s", exc)
            return False, f"Rejection check error: {exc}"
        return True, ""

    # ?????? New filters ?????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????
    # Note: daily realized volatility is no longer a hard veto. High-atr% trades
    # keep positive expectancy, so they are sized DOWN via
    # math_utils.volatility_size_factor at position-sizing time (live + backtest)
    # rather than blocked here. See app/utils/math_utils.py.

    def _premium_discount_alignment(self, sig: "SignalResult") -> tuple[bool, str]:
        """
        Block SELL signals in discount/deep_discount zones ??? price is cheap,
        not expensive. Block BUY signals in premium/deep_premium zones.
        Selling discount and buying premium are structurally counter-trend entries.
        """
        pd = (sig.premium_discount or "").lower().replace(" ", "_")
        if sig.signal == "SELL" and pd in _SELL_BLOCKED_PD:
            return False, (
                f"SELL blocked in {sig.premium_discount} zone ??? "
                f"price is in discount territory (buy zone, not sell zone)"
            )
        if sig.signal == "BUY" and pd in _BUY_BLOCKED_PD:
            return False, (
                f"BUY blocked in {sig.premium_discount} zone ??? "
                f"price is in premium territory (sell zone, not buy zone)"
            )
        return True, ""

    def _distribution_regime(self, sig: "SignalResult") -> tuple[bool, str]:
        """
        Block entries in distribution (ranging/choppy) regimes.
        Win rate in distribution was 50% ??? coin-flip, no edge.
        """
        cfg = get_settings()
        forced_on = sig.symbol.upper() in cfg.filter_distribution_symbol_set
        if not cfg.filter_distribution_enabled and not forced_on:
            return True, ""
        regime = (sig.market_regime or "").lower()
        if regime in _NO_TRADE_REGIMES:
            return False, (
                f"Regime '{sig.market_regime}' has no statistical edge ??? "
                f"distribution/ranging markets produce 50% WR"
            )
        return True, ""

    def _trap_zone(self, sig: "SignalResult") -> tuple[bool, str]:
        """
        Trap-zone entries need stronger conviction ??? large wicks + high volume
        indicate failed-breakout conditions. Matches the backtest gate that
        lived in engine.py before v5 moved it here for live/paper parity.
        """
        regime = (sig.market_regime or "").lower()
        if regime != "trap_zone":
            return True, ""
        cfg = get_settings()
        if sig.confidence < cfg.trap_zone_min_confidence:
            return False, (
                f"Trap-zone confidence {sig.confidence:.1f} < "
                f"min {cfg.trap_zone_min_confidence:.1f}"
            )
        return True, ""

    def _time_of_day(self, sig: "SignalResult") -> tuple[bool, str]:
        """
        Block entries during UTC hours with historically 0% win rate.
        Low-liquidity Asia dead zone (01-04) and NY chop hours (13, 15, 23)
        consistently produce stopped trades.
        """
        if not get_settings().filter_hours_enabled:
            return True, ""
        ref_s = (self._now_ms / 1000) if self._now_ms else time.time()
        utc_hour = time.gmtime(ref_s).tm_hour
        if utc_hour in _BLOCKED_HOURS_UTC:
            return False, (
                f"Entry blocked at {utc_hour:02d}:xx UTC ??? "
                f"historically 0% win rate at this hour"
            )
        return True, ""

    def _candle_rejection(self, sig: "SignalResult") -> tuple[bool, str]:
        """
        Require the most recent closed 30m candle to show price rejection
        in the signal direction before committing to entry.

        12 of 15 losing trades had MFE=$0 ??? price never moved in our favour.
        This filter blocks entries where the last candle is a strong momentum
        candle *against* the signal (no rejection evidence at the zone).

        SELL: block if last candle is strongly bullish with no upper wick.
        BUY:  block if last candle is strongly bearish with no lower wick.
        """
        try:
            if self._df_30m is not None:
                # Backtest path: caller supplies the lookahead-safe slice whose
                # last row IS the last fully closed candle.
                df = self._df_30m
                if len(df) < 1:
                    return True, ""
                c = df.iloc[-1]
            else:
                from app.data.repository import get_candles
                df = get_candles(sig.symbol, "30m", limit=3)
                if df is None or len(df) < 2:
                    return True, ""  # can't check ??? allow through
                # Use the second-to-last candle (last fully closed candle)
                c = df.iloc[-2]
            o, h, l, cl = float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"])
            candle_range = h - l
            if candle_range == 0:
                return True, ""  # doji / flat ??? no signal either way

            body      = abs(cl - o)
            body_pct  = body / candle_range
            upper_wick = h - max(o, cl)
            lower_wick = min(o, cl) - l
            upper_wick_pct = upper_wick / candle_range
            lower_wick_pct = lower_wick / candle_range

            if sig.signal == "SELL":
                # Strong bullish momentum candle with no upper wick = no rejection
                is_strongly_bullish = cl > o and body_pct >= _MOMENTUM_BODY_THRESHOLD
                has_upper_rejection = upper_wick_pct >= _REJECTION_WICK_THRESHOLD
                if is_strongly_bullish and not has_upper_rejection:
                    return False, (
                        f"No SELL rejection on last candle ??? "
                        f"bullish body {body_pct:.0%}, upper wick {upper_wick_pct:.0%} "
                        f"(need wick ???{_REJECTION_WICK_THRESHOLD:.0%} or bearish close)"
                    )

            elif sig.signal == "BUY":
                # Strong bearish momentum candle with no lower wick = no rejection
                is_strongly_bearish = cl < o and body_pct >= _MOMENTUM_BODY_THRESHOLD
                has_lower_rejection = lower_wick_pct >= _REJECTION_WICK_THRESHOLD
                if is_strongly_bearish and not has_lower_rejection:
                    return False, (
                        f"No BUY rejection on last candle ??? "
                        f"bearish body {body_pct:.0%}, lower wick {lower_wick_pct:.0%} "
                        f"(need wick ???{_REJECTION_WICK_THRESHOLD:.0%} or bullish close)"
                    )

        except Exception as exc:
            logger.warning("[Filter] candle_rejection check failed: %s", exc)

        return True, ""

    def _macro_events(self, sig: "SignalResult") -> tuple[bool, str]:
        """
        Block entries within ?? macro_guard_hours of scheduled macro events
        (FOMC decisions, CPI releases ??? data/macro_events.json). These cause
        violent whipsaws that zone logic cannot anticipate.
        """
        cfg = get_settings()
        if not cfg.macro_guard_enabled:
            return True, ""
        try:
            events = _load_macro_events(cfg.macro_events_file)
            if not events:
                return True, ""
            ref_ms = self._now_ms or int(time.time() * 1000)
            window_ms = cfg.macro_guard_hours * 3_600_000
            for name, event_ms in events:
                if abs(ref_ms - event_ms) <= window_ms:
                    return False, (
                        f"Macro guard: within ??{cfg.macro_guard_hours:.0f}h of {name}"
                    )
        except Exception as exc:
            logger.warning("[Filter] macro_events check failed: %s", exc)
        return True, ""

    def _funding_rate(self, sig: "SignalResult") -> tuple[bool, str]:
        """
        Futures sentiment guard. Shorting when funding is already strongly
        negative means joining a crowded short (squeeze risk) ??? and vice
        versa for longs.

        Live: current rate from the API (cached 5 min).
        Backtest (df_30m provided): stored historical rate at the candle
        time (funding_rates table; populated by scripts/backfill_funding.py).
        Skips silently when no rate is available either way.
        """
        cfg = get_settings()
        if not cfg.funding_filter_enabled:
            return True, ""
        try:
            if self._df_30m is not None:
                from app.data.repository import get_funding_rate_at
                rate = get_funding_rate_at(
                    sig.symbol, self._now_ms or int(time.time() * 1000)
                )
            else:
                rate = _cached_funding_rate(sig.symbol)
            if rate is None:
                return True, ""
            limit = cfg.funding_rate_limit
            if sig.signal == "SELL" and rate < -limit:
                return False, (
                    f"Funding {rate * 100:.3f}% < -{limit * 100:.3f}% ??? "
                    f"crowded short, squeeze risk"
                )
            if sig.signal == "BUY" and rate > limit:
                return False, (
                    f"Funding {rate * 100:.3f}% > +{limit * 100:.3f}% ??? "
                    f"crowded long, flush risk"
                )
        except Exception as exc:
            logger.warning("[Filter] funding_rate check failed: %s", exc)
        return True, ""


# ?????? Module-level caches ???????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????

_macro_cache: dict = {"mtime": None, "events": []}


def _load_macro_events(path) -> list[tuple[str, int]]:
    """Parse data/macro_events.json -> [(name, utc_ms)], cached by file mtime."""
    import json
    import os
    from datetime import datetime, timezone

    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return []
    if _macro_cache["mtime"] == mtime:
        return _macro_cache["events"]

    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    events: list[tuple[str, int]] = []
    for e in raw.get("events", []):
        ts = e.get("utc", "").replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            events.append((e.get("name", "event"), int(dt.timestamp() * 1000)))
        except ValueError:
            continue
    _macro_cache["mtime"] = mtime
    _macro_cache["events"] = events
    return events


_funding_cache: dict[str, tuple[float, float]] = {}   # symbol -> (rate, fetched_at)
_FUNDING_TTL_S = 300


def _cached_funding_rate(symbol: str) -> float | None:
    """Funding rate with a 5-minute cache so each signal cycle costs ???1 call."""
    now = time.time()
    hit = _funding_cache.get(symbol)
    if hit and now - hit[1] < _FUNDING_TTL_S:
        return hit[0]
    try:
        from app.data.binance_client import BinanceClient
        with BinanceClient() as client:
            rate = client.get_funding_rate(symbol)
        _funding_cache[symbol] = (rate, now)
        return rate
    except Exception as exc:
        logger.warning("[Filter] funding fetch failed for %s: %s", symbol, exc)
        return None

