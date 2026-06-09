"""Liquidity pool detection — swing highs/lows, equal levels, stop-hunt areas."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.ta.market_structure import find_swings


_EQUAL_LEVEL_TOLERANCE = 0.002  # 0.2% tolerance for "equal" highs/lows


def detect_liquidity_levels(df: pd.DataFrame, swing_len: int = 5) -> list[dict]:
    """
    Identify liquidity pools from a candle DataFrame.

    Returns a list of level dicts with keys:
        level_type, price, open_time, swept
    """
    df = find_swings(df, swing_len)
    levels: list[dict] = []

    sh_df = df[df["swing_high"].notna()].copy()
    sl_df = df[df["swing_low"].notna()].copy()

    # Swing highs / lows
    for idx, row in sh_df.iterrows():
        open_time = _to_ms(idx)
        levels.append({
            "level_type": "swing_high",
            "price":      round(float(row["swing_high"]), 4),
            "open_time":  open_time,
            "swept":      0,
        })

    for idx, row in sl_df.iterrows():
        open_time = _to_ms(idx)
        levels.append({
            "level_type": "swing_low",
            "price":      round(float(row["swing_low"]), 4),
            "open_time":  open_time,
            "swept":      0,
        })

    # Equal highs / lows (within tolerance)
    if len(sh_df) >= 2:
        sh_prices = sh_df["swing_high"].values
        sh_times  = [_to_ms(i) for i in sh_df.index]
        for i in range(len(sh_prices) - 1):
            for j in range(i + 1, len(sh_prices)):
                if _are_equal(sh_prices[i], sh_prices[j]):
                    levels.append({
                        "level_type": "equal_high",
                        "price":      round(float((sh_prices[i] + sh_prices[j]) / 2), 4),
                        "open_time":  sh_times[j],
                        "swept":      0,
                    })

    if len(sl_df) >= 2:
        sl_prices = sl_df["swing_low"].values
        sl_times  = [_to_ms(i) for i in sl_df.index]
        for i in range(len(sl_prices) - 1):
            for j in range(i + 1, len(sl_prices)):
                if _are_equal(sl_prices[i], sl_prices[j]):
                    levels.append({
                        "level_type": "equal_low",
                        "price":      round(float((sl_prices[i] + sl_prices[j]) / 2), 4),
                        "open_time":  sl_times[j],
                        "swept":      0,
                    })

    # Previous day high / low (only meaningful on intraday TFs)
    pdh, pdl = _prev_day_hl(df)
    if pdh is not None:
        levels.append({"level_type": "pdh", "price": round(pdh, 4),
                       "open_time": 0, "swept": 0})
    if pdl is not None:
        levels.append({"level_type": "pdl", "price": round(pdl, 4),
                       "open_time": 0, "swept": 0})

    return levels


def detect_sweeps(df: pd.DataFrame, levels: list[dict]) -> list[dict]:
    """
    Mark levels that have been swept (price crossed, then returned).

    Modifies the levels list in-place and returns updated copy.
    """
    if df.empty or not levels:
        return levels

    recent = df.tail(5)

    for lvl in levels:
        if lvl.get("swept"):
            continue
        price = lvl["price"]
        lt    = lvl["level_type"]

        if "high" in lt or lt == "pdh":
            # Swept if price poked above and then closed below
            for _, row in recent.iterrows():
                if row["high"] > price and row["close"] < price:
                    lvl["swept"] = 1
                    break
        elif "low" in lt or lt == "pdl":
            for _, row in recent.iterrows():
                if row["low"] < price and row["close"] > price:
                    lvl["swept"] = 1
                    break

    return levels


def liquidity_was_swept_below(df: pd.DataFrame, price: float, lookback: int = 10) -> bool:
    """Return True if sell-side liquidity below `price` was swept recently."""
    recent = df.tail(lookback)
    for _, row in recent.iterrows():
        if row["low"] < price and row["close"] > price:
            return True
    return False


def liquidity_was_swept_above(df: pd.DataFrame, price: float, lookback: int = 10) -> bool:
    """Return True if buy-side liquidity above `price` was swept recently."""
    recent = df.tail(lookback)
    for _, row in recent.iterrows():
        if row["high"] > price and row["close"] < price:
            return True
    return False


# ── Internal helpers ──────────────────────────────────────────────────────────

def _are_equal(a: float, b: float) -> bool:
    if a == 0:
        return False
    return abs(a - b) / abs(a) <= _EQUAL_LEVEL_TOLERANCE


def _to_ms(idx) -> int:
    if isinstance(idx, pd.Timestamp):
        return int(idx.timestamp() * 1000)
    return int(idx)


def _prev_day_hl(df: pd.DataFrame) -> tuple[float | None, float | None]:
    """Return previous day high/low if DataFrame has enough data."""
    try:
        daily = df.resample("1D").agg({"high": "max", "low": "min"}).dropna()
        if len(daily) < 2:
            return None, None
        prev = daily.iloc[-2]
        return float(prev["high"]), float(prev["low"])
    except Exception:
        return None, None
