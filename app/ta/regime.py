"""Market regime classification engine."""

from __future__ import annotations

import pandas as pd

from app.ta.indicators import add_indicators
from app.ta.market_structure import detect_structure, find_swings
from app.utils.math_utils import atr


class Regime:
    BULLISH_TREND   = "bullish_trend"
    BEARISH_TREND   = "bearish_trend"
    ACCUMULATION    = "accumulation"
    DISTRIBUTION    = "distribution"
    TRAP_ZONE       = "trap_zone"
    SQUEEZE         = "squeeze"
    HIGH_VOL_LIQ    = "high_volatility_liquidation"
    CHOPPY          = "choppy"


_REGIME_TRADE_RULES = {
    Regime.BULLISH_TREND:   ["BUY"],
    Regime.BEARISH_TREND:   ["SELL"],
    Regime.ACCUMULATION:    ["BUY"],
    Regime.DISTRIBUTION:    ["SELL"],
    Regime.TRAP_ZONE:       ["BUY", "SELL"],  # after confirmation
    Regime.SQUEEZE:         ["HOLD"],
    Regime.HIGH_VOL_LIQ:    ["HOLD"],
    Regime.CHOPPY:          ["HOLD"],
}


def classify_regime(df: pd.DataFrame) -> dict:
    """
    Classify the current market regime.

    Returns:
        regime         : Regime string
        allowed_signals: list of allowed signal types
        details        : list[str]
        score          : dict of per-indicator scores
    """
    if len(df) < 50:
        return {
            "regime": Regime.CHOPPY,
            "allowed_signals": ["HOLD"],
            "details": ["Insufficient data for regime classification"],
            "score": {},
        }

    if "ema_50" not in df.columns:
        df = add_indicators(df)
    last = df.iloc[-1]
    details = []
    score: dict[str, str] = {}

    close   = float(last["close"])
    ema50   = float(last["ema_50"])
    ema200  = float(last["ema_200"])
    atr_now = float(last["atr_14"])
    rsi_val = float(last["rsi_14"])
    vol     = float(last["volume"])
    vol_ma  = float(last["vol_ma_20"])

    # ATR history for squeeze / expansion detection
    atr_series = df["atr_14"].dropna()
    atr_50_avg = float(atr_series.rolling(50).mean().iloc[-1]) if len(atr_series) >= 50 else atr_now
    atr_ratio  = atr_now / atr_50_avg if atr_50_avg > 0 else 1.0

    # Market structure
    struct = detect_structure(df)
    structure = struct["structure"]

    # ── High-volatility liquidation ───────────────────────────────────────────
    if atr_ratio > 2.5:
        details.append(f"ATR ratio {atr_ratio:.1f}x — high-volatility / liquidation event")
        # Large wicks indicate liquidation
        upper_wick = float(last["upper_wick"])
        lower_wick = float(last["lower_wick"])
        if upper_wick > atr_now * 0.5 or lower_wick > atr_now * 0.5:
            details.append("Large wicks confirm liquidation activity")
            score["vol"] = "extreme"
            return {
                "regime": Regime.HIGH_VOL_LIQ,
                "allowed_signals": _REGIME_TRADE_RULES[Regime.HIGH_VOL_LIQ],
                "details": details,
                "score": score,
            }

    # ── Volatility squeeze ────────────────────────────────────────────────────
    if atr_ratio < 0.5:
        details.append(f"ATR ratio {atr_ratio:.2f} — volatility squeeze")
        score["vol"] = "squeeze"
        return {
            "regime": Regime.SQUEEZE,
            "allowed_signals": _REGIME_TRADE_RULES[Regime.SQUEEZE],
            "details": details,
            "score": score,
        }

    # ── EMA alignment check ───────────────────────────────────────────────────
    bull_ema = ema50 > ema200
    bear_ema = ema50 < ema200

    # ── Trap zone: failed breakout ────────────────────────────────────────────
    # Recent BOS followed by quick reversal
    last_bos = struct.get("last_bos")
    last_sh  = struct.get("last_swing_high", close)
    last_sl  = struct.get("last_swing_low", close)

    wick_ratio = (float(last["upper_wick"]) + float(last["lower_wick"])) / (float(last["body"]) + 0.0001)
    if wick_ratio > 2 and vol > vol_ma * 1.5:
        details.append(f"Large wicks + high volume — possible trap zone (wick ratio {wick_ratio:.1f})")
        score["trap"] = "yes"
        return {
            "regime": Regime.TRAP_ZONE,
            "allowed_signals": _REGIME_TRADE_RULES[Regime.TRAP_ZONE],
            "details": details,
            "score": score,
        }

    # ── Choppy / undefined ────────────────────────────────────────────────────
    if structure == "ranging" or structure == "undefined":
        # Use EMA 50/200 alignment to determine bias even in choppy/ranging markets.
        # A ranging market ABOVE the medium-term trend is consolidation (accumulation).
        # A ranging market BELOW the medium-term trend is consolidation (distribution).
        if bull_ema:
            details.append(f"Structure: {structure} but EMA 50>200 — bullish consolidation/accumulation")
            score["phase"] = "consolidation_bull"
            return {
                "regime": Regime.ACCUMULATION,
                "allowed_signals": _REGIME_TRADE_RULES[Regime.ACCUMULATION],
                "details": details,
                "score": score,
            }
        elif bear_ema:
            details.append(f"Structure: {structure} but EMA 50<200 — bearish consolidation/distribution")
            score["phase"] = "consolidation_bear"
            return {
                "regime": Regime.DISTRIBUTION,
                "allowed_signals": _REGIME_TRADE_RULES[Regime.DISTRIBUTION],
                "details": details,
                "score": score,
            }

        details.append(f"Structure: {structure} + EMAs intertwined — choppy / no clear direction")
        score["structure"] = structure
        return {
            "regime": Regime.CHOPPY,
            "allowed_signals": _REGIME_TRADE_RULES[Regime.CHOPPY],
            "details": details,
            "score": score,
        }

    # ── Trend detection ───────────────────────────────────────────────────────
    if structure == "bullish" and bull_ema:
        details.append("HH/HL structure + EMA 50>200 — bullish trend")
        return {
            "regime": Regime.BULLISH_TREND,
            "allowed_signals": _REGIME_TRADE_RULES[Regime.BULLISH_TREND],
            "details": details,
            "score": {"trend": "bullish"},
        }

    if structure == "bullish" and not bull_ema:
        # Structure is already bullish (HH/HL) but EMA cross hasn't happened yet.
        # This is the early accumulation / recovery phase — BUY signals should be allowed.
        details.append("HH/HL structure + EMA 50<200 — early bull / accumulation phase")
        return {
            "regime": Regime.ACCUMULATION,
            "allowed_signals": _REGIME_TRADE_RULES[Regime.ACCUMULATION],
            "details": details,
            "score": {"phase": "early_bull"},
        }

    if structure == "bearish" and bear_ema:
        details.append("LH/LL structure + EMA 50<200 — bearish trend")
        return {
            "regime": Regime.BEARISH_TREND,
            "allowed_signals": _REGIME_TRADE_RULES[Regime.BEARISH_TREND],
            "details": details,
            "score": {"trend": "bearish"},
        }

    if structure == "bearish" and not bear_ema:
        # Structure is already bearish but EMA hasn't confirmed yet — distribution phase.
        details.append("LH/LL structure + EMA 50>200 — early bear / distribution phase")
        return {
            "regime": Regime.DISTRIBUTION,
            "allowed_signals": _REGIME_TRADE_RULES[Regime.DISTRIBUTION],
            "details": details,
            "score": {"phase": "early_bear"},
        }

    # Fallback
    details.append(f"Mixed signals — structure={structure}, EMA_bull={bull_ema}")
    return {
        "regime": Regime.CHOPPY,
        "allowed_signals": _REGIME_TRADE_RULES[Regime.CHOPPY],
        "details": details,
        "score": {},
    }
