"""Market structure — swing detection, BOS, CHoCH."""

from __future__ import annotations

import pandas as pd
import numpy as np


def find_swings(df: pd.DataFrame, swing_len: int = 5) -> pd.DataFrame:
    """
    Find swing highs and lows using a pivot detection approach.

    A swing high at index i: high[i] is the highest in [i-swing_len, i+swing_len].
    A swing low  at index i: low[i]  is the lowest  in [i-swing_len, i+swing_len].

    Returns DataFrame with columns: swing_high, swing_low (NaN where not a swing).
    """
    df = df.copy()
    highs = df["high"].values
    lows  = df["low"].values
    n = len(df)

    swing_highs = np.full(n, np.nan)
    swing_lows  = np.full(n, np.nan)

    for i in range(swing_len, n - swing_len):
        window_h = highs[i - swing_len: i + swing_len + 1]
        window_l = lows[i  - swing_len: i + swing_len + 1]

        if highs[i] == window_h.max():
            swing_highs[i] = highs[i]
        if lows[i] == window_l.min():
            swing_lows[i] = lows[i]

    df["swing_high"] = swing_highs
    df["swing_low"]  = swing_lows
    return df


def detect_structure(df: pd.DataFrame, swing_len: int = 5) -> dict:
    """
    Detect market structure: HH/HL (bullish), LH/LL (bearish), BOS, CHoCH.

    Returns:
        structure       : bullish | bearish | ranging | undefined
        last_bos        : bullish | bearish | None
        last_choch      : bullish | bearish | None
        last_swing_high : price
        last_swing_low  : price
        details         : list[str]
    """
    df = find_swings(df, swing_len)

    sh_series = df["swing_high"].dropna()
    sl_series = df["swing_low"].dropna()

    if len(sh_series) < 2 or len(sl_series) < 2:
        return {
            "structure": "undefined",
            "last_bos": None,
            "last_choch": None,
            "last_swing_high": float(df["high"].iloc[-1]),
            "last_swing_low":  float(df["low"].iloc[-1]),
            "details": ["Not enough swings to determine structure"],
        }

    sh_vals = sh_series.values
    sl_vals = sl_series.values

    details = []
    last_bos = None
    last_choch = None

    # Determine HH/HL vs LH/LL
    hh = sh_vals[-1] > sh_vals[-2]
    hl = sl_vals[-1] > sl_vals[-2]
    lh = sh_vals[-1] < sh_vals[-2]
    ll = sl_vals[-1] < sl_vals[-2]

    if hh and hl:
        structure = "bullish"
        details.append("Higher High + Higher Low — bullish structure")
    elif lh and ll:
        structure = "bearish"
        details.append("Lower High + Lower Low — bearish structure")
    elif hh and ll:
        structure = "ranging"
        details.append("Higher High + Lower Low — ranging/expanding")
    elif lh and hl:
        structure = "ranging"
        details.append("Lower High + Higher Low — ranging/contracting")
    else:
        structure = "undefined"
        details.append("Structure unclear")

    # BOS: price broke above last swing high (bullish) or below swing low (bearish)
    current_close = float(df["close"].iloc[-1])
    last_sh = float(sh_vals[-2])  # previous swing high
    last_sl = float(sl_vals[-2])  # previous swing low

    if current_close > last_sh:
        last_bos = "bullish"
        details.append(f"Bullish BOS — price broke above swing high {last_sh:.2f}")
    elif current_close < last_sl:
        last_bos = "bearish"
        details.append(f"Bearish BOS — price broke below swing low {last_sl:.2f}")

    # CHoCH: structure flipped from bearish to bullish or vice versa
    if structure == "bullish" and (lh or ll):
        last_choch = "bullish"
        details.append("Bullish CHoCH — structure shifted from bearish")
    elif structure == "bearish" and (hh or hl):
        last_choch = "bearish"
        details.append("Bearish CHoCH — structure shifted from bullish")

    return {
        "structure": structure,
        "last_bos": last_bos,
        "last_choch": last_choch,
        "last_swing_high": round(float(sh_vals[-1]), 4),
        "last_swing_low":  round(float(sl_vals[-1]), 4),
        "prev_swing_high": round(float(sh_vals[-2]), 4),
        "prev_swing_low":  round(float(sl_vals[-2]), 4),
        "details": details,
    }


def get_range(df: pd.DataFrame, lookback: int = 50) -> dict:
    """Return the current trading range (high/low over lookback candles)."""
    window = df.tail(lookback)
    high = float(window["high"].max())
    low  = float(window["low"].min())
    mid  = (high + low) / 2
    current = float(df["close"].iloc[-1])

    pct_from_bottom = (current - low) / (high - low) * 100 if high != low else 50.0

    if pct_from_bottom >= 75:
        location = "premium"
    elif pct_from_bottom >= 50:
        location = "equilibrium"
    elif pct_from_bottom >= 25:
        location = "discount"
    else:
        location = "deep_discount"

    return {
        "range_high": round(high, 4),
        "range_low":  round(low, 4),
        "range_mid":  round(mid, 4),
        "current":    round(current, 4),
        "pct_from_bottom": round(pct_from_bottom, 1),
        "location":   location,
    }
