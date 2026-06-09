"""Trend detection — EMA alignment, directional bias."""

from __future__ import annotations

import pandas as pd

from app.ta.indicators import add_indicators


class TrendBias:
    STRONGLY_BULLISH = "strongly_bullish"
    BULLISH          = "bullish"
    NEUTRAL          = "neutral"
    BEARISH          = "bearish"
    STRONGLY_BEARISH = "strongly_bearish"


def detect_trend_bias(df: pd.DataFrame, lookback: int = 20) -> dict:
    """
    Classify the directional bias using EMA structure and price action.

    Returns:
        bias        : TrendBias string
        ema_alignment: bullish | bearish | mixed
        price_vs_ema50 : above | below
        price_vs_ema200: above | below
        slope_50    : positive | negative | flat
        score       : -2 .. +2 int (negative = bearish)
        details     : list[str]
    """
    if len(df) < 50:
        return {"bias": TrendBias.NEUTRAL, "score": 0, "details": ["Insufficient data"]}

    if "ema_50" not in df.columns:
        df = add_indicators(df)
    last = df.iloc[-1]

    score = 0
    details = []

    close   = float(last["close"])
    ema50   = float(last["ema_50"])
    ema200  = float(last["ema_200"])
    ema9    = float(last["ema_9"])
    ema21   = float(last["ema_21"])

    # 1. EMA cross alignment (9 > 21 > 50 > 200 = full bull)
    bull_alignment = ema9 > ema21 > ema50 > ema200
    bear_alignment = ema9 < ema21 < ema50 < ema200

    if bull_alignment:
        score += 2
        details.append("EMA 9>21>50>200 — full bullish alignment")
        ema_alignment = "bullish"
    elif bear_alignment:
        score -= 2
        details.append("EMA 9<21<50<200 — full bearish alignment")
        ema_alignment = "bearish"
    elif ema50 > ema200:
        score += 1
        details.append("EMA 50>200 — medium-term bullish")
        ema_alignment = "bullish"
    elif ema50 < ema200:
        score -= 1
        details.append("EMA 50<200 — medium-term bearish")
        ema_alignment = "bearish"
    else:
        ema_alignment = "mixed"

    # 2. Price vs EMA 50
    if close > ema50:
        score += 1
        details.append("Price above EMA 50")
        price_vs_ema50 = "above"
    else:
        score -= 1
        details.append("Price below EMA 50")
        price_vs_ema50 = "below"

    # 3. Price vs EMA 200
    if close > ema200:
        score += 1
        details.append("Price above EMA 200")
        price_vs_ema200 = "above"
    else:
        score -= 1
        details.append("Price below EMA 200")
        price_vs_ema200 = "below"

    # 4. EMA 50 slope
    ema50_slope = float(df["ema_50"].iloc[-1]) - float(df["ema_50"].iloc[-5])
    if ema50_slope > 0:
        slope_50 = "positive"
        score += 1
        details.append("EMA 50 slope rising")
    elif ema50_slope < 0:
        slope_50 = "negative"
        score -= 1
        details.append("EMA 50 slope falling")
    else:
        slope_50 = "flat"

    # Classify
    if score >= 4:
        bias = TrendBias.STRONGLY_BULLISH
    elif score >= 1:
        bias = TrendBias.BULLISH
    elif score <= -4:
        bias = TrendBias.STRONGLY_BEARISH
    elif score <= -1:
        bias = TrendBias.BEARISH
    else:
        bias = TrendBias.NEUTRAL

    return {
        "bias": bias,
        "ema_alignment": ema_alignment,
        "price_vs_ema50": price_vs_ema50,
        "price_vs_ema200": price_vs_ema200,
        "slope_50": slope_50,
        "score": score,
        "ema_50": round(ema50, 4),
        "ema_200": round(ema200, 4),
        "close": round(close, 4),
        "details": details,
    }


def is_bullish_bias(bias: str) -> bool:
    return bias in (TrendBias.BULLISH, TrendBias.STRONGLY_BULLISH)


def is_bearish_bias(bias: str) -> bool:
    return bias in (TrendBias.BEARISH, TrendBias.STRONGLY_BEARISH)
