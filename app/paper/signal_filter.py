"""
SignalFilter — ordered pre-trade filter pipeline.

A SignalResult only becomes a trade if it passes every filter.
Each filter returns (pass: bool, reason: str).  The first failure
short-circuits; the reason is logged and shown in the watcher output.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from app.config import get_settings
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

# Market regimes that are choppy / no-edge — skip entries
_NO_TRADE_REGIMES = {"distribution"}

# UTC hours (0-23) where historical win rate is 0% — skip entries
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

    def evaluate(self, sig: "SignalResult") -> tuple[bool, str]:
        """
        Run every filter in order.
        Returns (True, '') if all pass, or (False, reason) on first failure.
        """
        for check in (
            self._null_confidence,
            self._zone_rating,
            self._zone_score,
            self._daily_bias,
            self._confidence,
            self._risk_reward,
            self._premium_discount_alignment,
            self._distribution_regime,
            self._time_of_day,
            self._candle_rejection,
        ):
            ok, reason = check(sig)
            if not ok:
                logger.info(
                    "[Filter][%s] %s blocked — %s",
                    sig.symbol, sig.signal, reason,
                )
                return False, reason

        return True, ""

    # ── Existing filters ──────────────────────────────────────────────────────

    def _null_confidence(self, sig: "SignalResult") -> tuple[bool, str]:
        """Reject signals with no confidence data (missing or zero — bad signal origin)."""
        if not sig.confidence:
            return False, "Confidence is zero or missing — signal data incomplete"
        return True, ""

    def _zone_rating(self, sig: "SignalResult") -> tuple[bool, str]:
        """Reject C-rated and unrated zones — insufficient confluence."""
        if sig.zone_rating in _BAD_RATINGS:
            return False, f"Zone rating '{sig.zone_rating}' below minimum (need A/A+/B)"
        return True, ""

    def _zone_score(self, sig: "SignalResult") -> tuple[bool, str]:
        """Reject if zone score is below the configured minimum."""
        cfg = get_settings()
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
        cfg = get_settings()
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

    # ── New filters ───────────────────────────────────────────────────────────

    def _premium_discount_alignment(self, sig: "SignalResult") -> tuple[bool, str]:
        """
        Block SELL signals in discount/deep_discount zones — price is cheap,
        not expensive. Block BUY signals in premium/deep_premium zones.
        Selling discount and buying premium are structurally counter-trend entries.
        """
        pd = (sig.premium_discount or "").lower().replace(" ", "_")
        if sig.signal == "SELL" and pd in _SELL_BLOCKED_PD:
            return False, (
                f"SELL blocked in {sig.premium_discount} zone — "
                f"price is in discount territory (buy zone, not sell zone)"
            )
        if sig.signal == "BUY" and pd in _BUY_BLOCKED_PD:
            return False, (
                f"BUY blocked in {sig.premium_discount} zone — "
                f"price is in premium territory (sell zone, not buy zone)"
            )
        return True, ""

    def _distribution_regime(self, sig: "SignalResult") -> tuple[bool, str]:
        """
        Block entries in distribution (ranging/choppy) regimes.
        Win rate in distribution was 50% — coin-flip, no edge.
        """
        regime = (sig.market_regime or "").lower()
        if regime in _NO_TRADE_REGIMES:
            return False, (
                f"Regime '{sig.market_regime}' has no statistical edge — "
                f"distribution/ranging markets produce 50% WR"
            )
        return True, ""

    def _time_of_day(self, sig: "SignalResult") -> tuple[bool, str]:
        """
        Block entries during UTC hours with historically 0% win rate.
        Low-liquidity Asia dead zone (01-04) and NY chop hours (13, 15, 23)
        consistently produce stopped trades.
        """
        utc_hour = time.gmtime().tm_hour
        if utc_hour in _BLOCKED_HOURS_UTC:
            return False, (
                f"Entry blocked at {utc_hour:02d}:xx UTC — "
                f"historically 0% win rate at this hour"
            )
        return True, ""

    def _candle_rejection(self, sig: "SignalResult") -> tuple[bool, str]:
        """
        Require the most recent closed 30m candle to show price rejection
        in the signal direction before committing to entry.

        12 of 15 losing trades had MFE=$0 — price never moved in our favour.
        This filter blocks entries where the last candle is a strong momentum
        candle *against* the signal (no rejection evidence at the zone).

        SELL: block if last candle is strongly bullish with no upper wick.
        BUY:  block if last candle is strongly bearish with no lower wick.
        """
        try:
            from app.data.repository import get_candles
            df = get_candles(sig.symbol, "30m", limit=3)
            if df is None or len(df) < 2:
                return True, ""  # can't check — allow through

            # Use the second-to-last candle (last fully closed candle)
            c = df.iloc[-2]
            o, h, l, cl = float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"])
            candle_range = h - l
            if candle_range == 0:
                return True, ""  # doji / flat — no signal either way

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
                        f"No SELL rejection on last candle — "
                        f"bullish body {body_pct:.0%}, upper wick {upper_wick_pct:.0%} "
                        f"(need wick ≥{_REJECTION_WICK_THRESHOLD:.0%} or bearish close)"
                    )

            elif sig.signal == "BUY":
                # Strong bearish momentum candle with no lower wick = no rejection
                is_strongly_bearish = cl < o and body_pct >= _MOMENTUM_BODY_THRESHOLD
                has_lower_rejection = lower_wick_pct >= _REJECTION_WICK_THRESHOLD
                if is_strongly_bearish and not has_lower_rejection:
                    return False, (
                        f"No BUY rejection on last candle — "
                        f"bearish body {body_pct:.0%}, lower wick {lower_wick_pct:.0%} "
                        f"(need wick ≥{_REJECTION_WICK_THRESHOLD:.0%} or bullish close)"
                    )

        except Exception as exc:
            logger.warning("[Filter] candle_rejection check failed: %s", exc)

        return True, ""
