"""Supply and demand zone detection using displacement-based logic."""

from __future__ import annotations

import pandas as pd
import numpy as np

from app.ta.indicators import add_indicators


# A displacement candle body must be >= 0.6x ATR to qualify (relaxed from 0.8)
_MIN_BODY_ATR_RATIO = 0.6
# Zone is fully mitigated when price closes past its far edge (not just midpoint)
_MITIGATION_PCT = 0.75


def detect_zones(df: pd.DataFrame, lookback: int = 300, timeframe: str = "30m") -> list[dict]:
    """
    Detect supply and demand zones from displacement candles.

    Algorithm:
    1. Find candles where body/ATR > threshold (displacement candles).
    2. The zone is the consolidation BEFORE the displacement (last 1???4 base candles).
    3. Discard zones fully mitigated by subsequent price action.

    Returns a list of zone dicts (unsorted, unscored), each tagged with source_tf.
    """
    if len(df) < 20:
        return []

    df = df.tail(lookback).copy()
    if "atr_14" not in df.columns:
        df = add_indicators(df)
    zones: list[dict] = []

    # Rolling average body for relative displacement comparison
    avg_body_series = df["body"].rolling(20, min_periods=5).mean()

    for i in range(5, len(df) - 1):
        row      = df.iloc[i]
        body     = float(row["body"])
        atr      = float(row.get("atr_14", 0)) or body
        avg_body = float(avg_body_series.iloc[i]) or body

        # Must be a displacement candle: body > 0.6x ATR and > average body
        if body / atr < _MIN_BODY_ATR_RATIO:
            continue
        if body < avg_body:          # only above-average moves qualify
            continue

        disp_close = float(row["close"])
        disp_open  = float(row["open"])
        disp_time  = _to_ms(df.index[i])
        is_bull    = disp_close > disp_open

        # Base: 1???4 candles immediately before the displacement
        base_start = max(0, i - 4)
        base       = df.iloc[base_start:i]
        if base.empty:
            continue

        base_high = float(base["high"].max())
        base_low  = float(base["low"].min())

        if base_high <= base_low:
            continue

        zone_type   = "demand" if is_bull else "supply"
        zone_top    = base_high
        zone_bottom = base_low

        # Discard if price has already closed fully through the zone
        # (past the _MITIGATION_PCT threshold of the zone height)
        subsequent = df.iloc[i + 1:] if i + 1 < len(df) else df.iloc[0:0]
        if not subsequent.empty:
            zone_height = zone_top - zone_bottom
            if is_bull:
                # Demand fully broken when close drops below zone_bottom - buffer
                broken_level = zone_bottom - zone_height * (1 - _MITIGATION_PCT)
                if (subsequent["close"] < broken_level).any():
                    continue
            else:
                # Supply fully broken when close rises above zone_top + buffer
                broken_level = zone_top + zone_height * (1 - _MITIGATION_PCT)
                if (subsequent["close"] > broken_level).any():
                    continue

        touch_count = _count_zone_touches(subsequent, zone_bottom, zone_top)

        zones.append({
            "zone_type":                zone_type,
            "zone_top":                 round(zone_top, 4),
            "zone_bottom":              round(zone_bottom, 4),
            "displacement_candle_time": disp_time,
            "body_atr_ratio":           round(body / atr, 3),
            "disp_volume":              float(row.get("volume", 0)),
            "base_candles":             len(base),
            "touch_count":              touch_count,
            "status":                   "active",
            "source_tf":                timeframe,
        })

    # De-duplicate overlapping zones (keep highest body_atr_ratio)
    zones = _dedup_zones(zones)
    return zones


def price_in_zone(price: float, zone: dict, buffer_pct: float = 0.001) -> bool:
    """Return True if price is within the zone (with optional buffer).

    buffer_pct is relative to the current price ??? e.g. 0.02 = within 2% of
    the zone boundary. This is intentionally price-relative so the buffer
    scales with volatility and doesn't depend on zone width, which varies
    wildly between 30-min and daily zones.
    """
    buffer = price * buffer_pct
    return zone["zone_bottom"] - buffer <= price <= zone["zone_top"] + buffer


def zone_midpoint(zone: dict) -> float:
    return (zone["zone_top"] + zone["zone_bottom"]) / 2


def is_zone_mitigated(zone: dict, df: pd.DataFrame) -> bool:
    """Check if a zone has been mitigated by recent price action."""
    mid = zone_midpoint(zone)
    if zone["zone_type"] == "demand":
        return bool((df["close"] < mid).any())
    else:
        return bool((df["close"] > mid).any())


# ?????? Internal helpers ??????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????

def _count_zone_touches(
    subsequent: pd.DataFrame, zone_bottom: float, zone_top: float
) -> int:
    """Count distinct retests after displacement (close re-enters zone)."""
    if subsequent.empty:
        return 0
    inside = (subsequent["close"] >= zone_bottom) & (subsequent["close"] <= zone_top)
    prev_inside = inside.shift(1, fill_value=False)
    return int((inside & ~prev_inside).sum())


def _dedup_zones(zones: list[dict]) -> list[dict]:
    """Remove overlapping zones, keeping those with higher body_atr_ratio."""
    if len(zones) <= 1:
        return zones

    kept = []
    for zone in sorted(zones, key=lambda z: z["body_atr_ratio"], reverse=True):
        overlap = False
        for k in kept:
            if k["zone_type"] != zone["zone_type"]:
                continue
            # Overlap if zones share any price range
            if zone["zone_bottom"] <= k["zone_top"] and zone["zone_top"] >= k["zone_bottom"]:
                overlap = True
                break
        if not overlap:
            kept.append(zone)
    return kept


def _to_ms(idx) -> int:
    if isinstance(idx, pd.Timestamp):
        return int(idx.timestamp() * 1000)
    return int(idx)

