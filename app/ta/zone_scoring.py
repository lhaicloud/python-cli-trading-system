"""Zone strength scoring — 0 to 100 across 8 dimensions."""

from __future__ import annotations

import pandas as pd

from app.ta.market_structure import get_range
from app.ta.zones import zone_midpoint
from app.utils.math_utils import risk_reward as calc_rr


def score_zone(
    zone: dict,
    df_m30: pd.DataFrame,
    h4_bias: str,
    daily_bias: str,
    liquidity_swept: bool = False,
    near_swing: bool = False,
    source_tf: str = "30m",
) -> dict:
    """
    Score a zone on 9 dimensions (8 base + timeframe quality bonus).

    Returns:
        score      : float 0–100
        rating     : A+ | A | B | C | ignore
        breakdown  : dict of per-dimension scores
    """
    breakdown: dict[str, float] = {}
    total = 0.0

    # ── 1. Displacement strength (0–20) ──────────────────────────────────────
    bar = zone.get("body_atr_ratio", 0)
    disp_vol = zone.get("disp_volume", 0)
    vol_ma = float(df_m30["volume"].rolling(20).mean().iloc[-1]) if not df_m30.empty else 1

    disp_score = min(20, bar * 8)  # 2.5 ratio → 20pts
    if disp_vol > vol_ma * 1.5:
        disp_score = min(20, disp_score + 4)
    breakdown["displacement_strength"] = round(disp_score, 1)
    total += disp_score

    # ── 2. Freshness (0–15) ──────────────────────────────────────────────────
    touch_count = zone.get("touch_count", 0)
    if touch_count == 0:
        fresh_score = 15.0
    elif touch_count == 1:
        fresh_score = 10.0
    elif touch_count == 2:
        fresh_score = 5.0
    else:
        fresh_score = 0.0
    breakdown["freshness"] = fresh_score
    total += fresh_score

    # ── 3. Liquidity context (0–15) ──────────────────────────────────────────
    liq_score = 0.0
    if liquidity_swept:
        liq_score += 10
    if near_swing:
        liq_score += 5
    breakdown["liquidity_context"] = min(15, liq_score)
    total += breakdown["liquidity_context"]

    # ── 4. Higher-timeframe alignment (0–15) ─────────────────────────────────
    htf_score = 0.0
    zt = zone["zone_type"]
    if zt == "demand":
        if "bullish" in h4_bias:
            htf_score += 10
        elif h4_bias == "neutral":
            htf_score += 3
        if "bullish" in daily_bias or "neutral" in daily_bias:
            htf_score += 5
    else:  # supply
        if "bearish" in h4_bias:
            htf_score += 10
        elif h4_bias == "neutral":
            htf_score += 3
        if "bearish" in daily_bias or "neutral" in daily_bias:
            htf_score += 5
    breakdown["htf_alignment"] = min(15, htf_score)
    total += breakdown["htf_alignment"]

    # ── 5. Premium / discount location (0–10) ────────────────────────────────
    rng = get_range(df_m30, lookback=100)
    loc = rng["location"]
    pd_score = 0.0
    if zt == "demand" and "discount" in loc:
        pd_score = 10.0
    elif zt == "supply" and "premium" in loc:
        pd_score = 10.0
    elif "equilibrium" in loc:
        pd_score = 3.0
    breakdown["premium_discount"] = pd_score
    total += pd_score

    # ── 6. Volume confirmation (0–10) ────────────────────────────────────────
    if not df_m30.empty and vol_ma > 0:
        last_vol = float(df_m30["volume"].iloc[-1])
        vol_ratio = last_vol / vol_ma
        vol_score = min(10, vol_ratio * 5)
    else:
        vol_score = 5.0
    breakdown["volume_confirmation"] = round(vol_score, 1)
    total += vol_score

    # ── 7. Risk/reward quality (0–10) ────────────────────────────────────────
    entry = zone_midpoint(zone)
    # ATR-based TP: use 4x ATR from entry as a realistic swing target.
    # SL = 0.75x ATR beyond zone edge (enough room to avoid premature stop-outs).
    try:
        atr_val = float(df_m30["atr_14"].iloc[-1]) if "atr_14" in df_m30.columns else (entry * 0.005)
    except Exception:
        atr_val = entry * 0.005

    if zt == "demand":
        sl = zone["zone_bottom"] - atr_val * 0.75       # SL just below zone
        tp = entry + atr_val * 4.0                      # TP = 4x ATR above entry
    else:
        sl = zone["zone_top"] + atr_val * 0.75          # SL just above zone
        tp = entry - atr_val * 4.0                      # TP = 4x ATR below entry

    # Clamp TP to a wider range (300 candles = ~150h) so trending markets
    # don't artificially compress the target.
    rng_wide = get_range(df_m30, lookback=300)
    rng_high = rng_wide["range_high"]
    rng_low  = rng_wide["range_low"]
    if zt == "demand" and tp > rng_high:
        tp = rng_high
    if zt == "supply" and tp < rng_low:
        tp = rng_low

    rr = calc_rr(entry, sl, tp)
    if rr >= 3:
        rr_score = 10.0
    elif rr >= 2:
        rr_score = 7.0
    elif rr >= 1.5:
        rr_score = 4.0
    else:
        rr_score = 0.0
    breakdown["risk_reward"] = rr_score
    total += rr_score

    # ── 8. Volatility quality (0–5) ──────────────────────────────────────────
    try:
        atr_now = float(df_m30["atr_14"].iloc[-1]) if "atr_14" in df_m30.columns else 0
        atr_avg = float(df_m30["atr_14"].rolling(50).mean().iloc[-1]) if "atr_14" in df_m30.columns else 1
        if atr_avg > 0:
            atr_ratio = atr_now / atr_avg
            if 0.5 <= atr_ratio <= 2.0:
                vol_q = 5.0
            elif atr_ratio > 3.0:
                vol_q = 1.0  # extremely high volatility
            elif atr_ratio > 2.0:
                vol_q = 2.5  # elevated but not extreme
            else:
                vol_q = 2.5  # below 0.5 — squeeze
        else:
            vol_q = 3.0
    except Exception:
        vol_q = 3.0
    breakdown["volatility_quality"] = vol_q
    total += vol_q

    # ── 9. Timeframe quality bonus (0–12) ────────────────────────────────────
    # Higher-TF zones reflect larger institutional moves and carry more weight.
    tf_bonus = {"1d": 12, "4h": 6, "30m": 0}.get(source_tf, 0)
    breakdown["timeframe_quality"] = float(tf_bonus)
    total += tf_bonus

    score = round(min(100, total), 1)

    if score >= 85:
        rating = "A+"
    elif score >= 75:
        rating = "A"
    elif score >= 65:
        rating = "B"
    elif score >= 50:
        rating = "C"
    else:
        rating = "ignore"

    return {
        "score":     score,
        "rating":    rating,
        "breakdown": breakdown,
        "rr_ratio":  round(rr, 2),
        "entry":     round(entry, 4),
        "sl":        round(sl, 4),
        "tp":        round(tp, 4),
    }
