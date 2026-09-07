"""
Signal generation engine — BUY / SELL / HOLD with confidence scoring.

Phase 3: Confluence model using HTF bias, market structure,
         liquidity, supply/demand zones, and volume.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path

# Set DISABLE_ML_MODEL=1 to run with pure rule-based confidence (for A/B comparison)
_USE_ML_MODEL = os.environ.get("DISABLE_ML_MODEL", "0") != "1"

import numpy as np
import pandas as pd

from app.version import ENGINE_VERSION

from app.config import get_settings, get_settings_for_symbol, is_simplified_strategy
from app.models.versioning import get_current_model_path
from app.ta.indicators import add_indicators
from app.ta.liquidity import detect_liquidity_levels, detect_sweeps, liquidity_was_swept_below, liquidity_was_swept_above
from app.ta.market_structure import detect_structure, get_range
from app.ta.regime import classify_regime, Regime
from app.ta.trend import detect_trend_bias, is_bullish_bias, is_bearish_bias
from app.ta.zones import detect_zones, price_in_zone, zone_midpoint
from app.ta.zone_scoring import score_zone
from app.models.buy_model import score_buy_setup
from app.models.sell_model import score_sell_setup
from app.utils.exceptions import DataNotReadyError
from app.utils.logger import get_logger
from app.utils.math_utils import risk_reward, position_size

logger = get_logger(__name__)

_SETUP_TYPES = {
    "trap_and_reclaim": "Trap-and-Reclaim",
    "pullback_demand":  "Pullback-to-Demand",
    "pullback_supply":  "Pullback-to-Supply",
    "breakout_retest":  "Breakout-Retest",
    "failed_breakout":  "Failed-Breakout Reversal",
}


@dataclass
class SignalResult:
    symbol:           str
    signal:           str           # BUY | SELL | HOLD | BLOCKED
    confidence:       float = 0.0
    market_regime:    str   = ""
    daily_bias:       str   = ""
    h4_bias:          str   = ""
    h1_confirmation:  str   = ""
    m30_zone_type:    str   = ""
    zone_score:       float = 0.0
    zone_rating:      str   = ""
    liquidity_sweep:  bool  = False
    premium_discount: str   = ""
    entry_price:      float = 0.0
    stop_loss:        float = 0.0
    take_profit:      float = 0.0
    risk_reward:      float = 0.0
    setup_type:       str   = ""
    reasons:          list  = field(default_factory=list)
    warnings:         list  = field(default_factory=list)
    model_version:    str   = ENGINE_VERSION
    data_quality:     str   = "unknown"
    timestamp:        int   = field(default_factory=lambda: int(time.time() * 1000))
    zone_id:          int | None = None
    # MTF scorer fields
    long_score:       float = 0.0
    short_score:      float = 0.0
    tf_scores:        list  = field(default_factory=list)
    rejection_reason: str | None = None
    # Daily realized volatility (atr_14/close*100) at signal time — drives the
    # volatility quality gate in SignalFilter. Rides on the dataclass so the
    # filter has it in backtest (where it only receives df_30m).
    daily_atr_pct:    float = 0.0


def generate_signal(
    symbol: str,
    df_1d: pd.DataFrame,
    df_4h: pd.DataFrame,
    df_1h: pd.DataFrame,
    df_30m: pd.DataFrame,
    capital: float = 10_000.0,
    data_quality: str = "ok",
    *,
    df_12h: pd.DataFrame,
    df_1w: pd.DataFrame,
) -> SignalResult:
    """
    Main signal generator.

    Inputs: OHLCV DataFrames for each timeframe (index = DatetimeIndex UTC).
    Returns: SignalResult with full confluence breakdown.
    """
    cfg = get_settings_for_symbol(symbol)
    reasons:  list[str] = []
    warnings: list[str] = []

    # Use stricter zone score floor for coins that have no active ML model
    _buy_mv_path  = get_current_model_path(symbol, "buy")
    _sell_mv_path = get_current_model_path(symbol, "sell")
    _has_model = _buy_mv_path is not None
    min_zone_score = cfg.min_zone_score if _has_model else cfg.min_zone_score_no_model

    # ── Add indicators to all timeframes (skip if already computed) ──────────
    _needs = lambda df: not df.empty and "ema_50" not in df.columns
    df_1d  = add_indicators(df_1d)  if _needs(df_1d)  else df_1d
    df_4h  = add_indicators(df_4h)  if _needs(df_4h)  else df_4h
    df_1h  = add_indicators(df_1h)  if _needs(df_1h)  else df_1h
    df_30m = add_indicators(df_30m) if _needs(df_30m) else df_30m
    df_12h = add_indicators(df_12h) if _needs(df_12h) else df_12h
    df_1w  = add_indicators(df_1w)  if _needs(df_1w)  else df_1w

    # ── Data quality gate ─────────────────────────────────────────────────────
    if data_quality != "ok":
        warnings.append(f"Data quality: {data_quality}")
    if df_30m.empty or len(df_30m) < 50:
        return _hold(symbol, "Insufficient 30M candle data", data_quality)
    if len(df_12h) < 50:
        raise DataNotReadyError(
            f"12H data insufficient for {symbol}. "
            f"Run: python main.py backfill --symbol {symbol} --start 2022-01-01"
        )
    if len(df_1w) < 52:
        raise DataNotReadyError(
            f"1W data insufficient for {symbol}. "
            f"Run: python main.py backfill --symbol {symbol} --start 2022-01-01"
        )

    if is_simplified_strategy():
        daily_atr_pct = 0.0
        if not df_1d.empty and "atr_pct" in df_1d.columns:
            try:
                daily_atr_pct = float(df_1d["atr_pct"].iloc[-1])
            except Exception:
                daily_atr_pct = 0.0
        return _generate_simple_signal(
            symbol, df_4h, df_1h, df_30m, capital, data_quality, daily_atr_pct,
        )

    current_price = float(df_30m["close"].iloc[-1])

    # Daily realized volatility (atr%/price) for the volatility quality gate.
    # Carried on the returned SignalResult so SignalFilter sees it in backtest
    # (where the filter only receives df_30m). Defaults to 0 = fail-open.
    daily_atr_pct = 0.0
    if not df_1d.empty and "atr_pct" in df_1d.columns:
        try:
            daily_atr_pct = float(df_1d["atr_pct"].iloc[-1])
        except Exception:
            daily_atr_pct = 0.0

    # ── 1. Daily macro filter ─────────────────────────────────────────────────
    daily_trend = detect_trend_bias(df_1d) if not df_1d.empty else {"bias": "neutral", "details": ["No daily data"]}
    daily_bias  = daily_trend["bias"]
    reasons.extend([f"[1D] {d}" for d in daily_trend.get("details", [])])

    # ── 2. Market regime (4H) ─────────────────────────────────────────────────
    regime_result = classify_regime(df_4h) if not df_4h.empty else {"regime": Regime.CHOPPY, "allowed_signals": ["HOLD"], "details": []}
    regime        = regime_result["regime"]
    allowed       = regime_result["allowed_signals"]
    reasons.extend([f"[Regime] {d}" for d in regime_result.get("details", [])])

    if "HOLD" in allowed and "BUY" not in allowed and "SELL" not in allowed:
        return _hold(symbol, f"Regime={regime} — HOLD only", data_quality, reasons, warnings, regime)

    # ── MTF pre-filter ────────────────────────────────────────────────────────
    from app.ta.mtf_scorer import run_prefilter
    prefilter = run_prefilter(df_1w, df_1d, df_12h, df_4h, df_1h, df_30m, cfg)
    reasons.append(
        f"[MTF] LONG={prefilter.long_score:.1f} SHORT={prefilter.short_score:.1f} "
        f"gap={prefilter.score_gap:.1f}"
    )

    if prefilter.decision == "BLOCKED":
        return _blocked(symbol, prefilter, data_quality, reasons, warnings, regime)
    if prefilter.decision == "HOLD":
        return _hold(symbol, prefilter.reason, data_quality, reasons, warnings, regime)

    # Regime must allow the MTF prefilter direction (bullish_trend → BUY only, etc.)
    wanted = "BUY" if prefilter.direction == "LONG" else "SELL"
    if wanted not in allowed:
        return _hold(
            symbol,
            f"Regime={regime} disallows {wanted} (allowed={allowed})",
            data_quality, reasons, warnings, regime,
        )

    # ── 3. 4H directional bias ────────────────────────────────────────────────
    h4_trend = detect_trend_bias(df_4h) if not df_4h.empty else {"bias": "neutral", "details": []}
    h4_bias  = h4_trend["bias"]
    h4_struct = detect_structure(df_4h) if not df_4h.empty else {"structure": "undefined", "details": []}
    reasons.extend([f"[4H] {d}" for d in h4_trend.get("details", [])])

    # ── 4. 1H confirmation ────────────────────────────────────────────────────
    h1_struct = detect_structure(df_1h) if not df_1h.empty else {"structure": "undefined", "details": []}
    h1_trend  = detect_trend_bias(df_1h) if not df_1h.empty else {"bias": "neutral", "details": []}
    h1_conf   = _h1_confirmation(h1_struct, h1_trend, df_1h)
    reasons.extend([f"[1H] {d}" for d in h1_conf.get("details", [])])

    # ── 5. Supply/demand zones across all timeframes ──────────────────────────
    # Merge 30m (6 days), 4H (33 days), and 1D (3 months) zones so institutional
    # levels from higher timeframes are visible alongside short-term structure.
    zones_raw = (
        detect_zones(df_30m, lookback=300, timeframe="30m")
        + detect_zones(df_4h,  lookback=200, timeframe="4h")
        + detect_zones(df_1d,  lookback=90,  timeframe="1d")
    )
    demand_zones = [z for z in zones_raw if z["zone_type"] == "demand"]
    supply_zones = [z for z in zones_raw if z["zone_type"] == "supply"]

    # ── 6. Liquidity detection ────────────────────────────────────────────────
    liq_levels = detect_liquidity_levels(df_30m)
    liq_levels = detect_sweeps(df_30m, liq_levels)

    # ── 7. Range / premium-discount ───────────────────────────────────────────
    rng = get_range(df_30m, lookback=100)
    pd_location = rng["location"]

    # ── 8 & 9. Evaluate only the prefilter direction ──────────────────────────
    buy_conf  = 0.0
    buy_zone  = None
    buy_score_result = {}
    buy_sweep = False
    buy_setup = ""
    sell_conf  = 0.0
    sell_zone  = None
    sell_score_result = {}
    sell_sweep = False
    sell_setup = ""

    if prefilter.direction == "LONG":
        buy_conf, buy_zone, buy_score_result, buy_sweep, buy_setup = _evaluate_buy(
            current_price, df_30m, df_1h,
            demand_zones, liq_levels, rng,
            h4_bias, daily_bias, h1_conf,
            reasons, min_zone_score,
        )
    else:  # SHORT
        sell_conf, sell_zone, sell_score_result, sell_sweep, sell_setup = _evaluate_sell(
            current_price, df_30m, df_1h,
            supply_zones, liq_levels, rng,
            h4_bias, daily_bias, h1_conf,
            reasons, min_zone_score,
        )

    # ── 10. Model probability adjustment (Phase 6) ───────────────────────────
    # Regime-adaptive blend: model is most valuable in declining/choppy regimes
    # (filters bad BUY signals) and least needed in strong trends (rules work well).
    # Multiplier controls max adjustment: factor=20 → ±10, factor=30 → ±15.
    _blend = _model_blend_factor(regime)
    _buy_model_version  = ENGINE_VERSION
    _sell_model_version = ENGINE_VERSION

    if _USE_ML_MODEL and buy_conf > 0 and buy_zone and buy_score_result:
        _mf = _build_model_features(h4_bias, h1_conf["status"], buy_score_result,
                                    buy_sweep, rng, df_30m, regime, "buy", df_1d=df_1d)
        _bp = score_buy_setup(symbol, _mf)
        if _bp > 0:
            buy_conf = round(min(100.0, max(0.0, buy_conf + (_bp - 0.5) * _blend)), 1)

    if _USE_ML_MODEL and sell_conf > 0 and sell_zone and sell_score_result:
        _mf = _build_model_features(h4_bias, h1_conf["status"], sell_score_result,
                                    sell_sweep, rng, df_30m, regime, "sell", df_1d=df_1d)
        _sp = score_sell_setup(symbol, _mf)
        if _sp > 0:
            sell_conf = round(min(100.0, max(0.0, sell_conf + (_sp - 0.5) * _blend)), 1)

    # ── 11. Signal conflict resolution ───────────────────────────────────────
    min_conf = cfg.min_signal_confidence

    both_strong = buy_conf >= min_conf and sell_conf >= min_conf
    both_weak   = buy_conf < min_conf  and sell_conf < min_conf

    if both_strong:
        warnings.append(f"BUY ({buy_conf:.0f}) and SELL ({sell_conf:.0f}) both strong — HOLD to avoid conflict")
        return _hold(symbol, "BUY/SELL conflict", data_quality, reasons, warnings, regime)

    if both_weak:
        return _hold(
            symbol,
            f"No strong signal — BUY={buy_conf:.0f}, SELL={sell_conf:.0f} (min={min_conf:.0f})",
            data_quality, reasons, warnings, regime,
        )

    # ── 12. ATR expansion — low-volatility warning ───────────────────────────
    if "atr_expansion" in df_30m.columns:
        try:
            atr_exp = float(df_30m["atr_expansion"].iloc[-1])
            if atr_exp < 0.7:
                warnings.append(
                    f"Low volatility: ATR expansion={atr_exp:.2f} — "
                    f"momentum may be absent, TP could take much longer"
                )
        except Exception:
            pass

    # ── 13. Final signal selection ────────────────────────────────────────────
    if buy_conf >= sell_conf and buy_conf >= min_conf and buy_zone:
        result = _build_buy_signal(
            symbol, buy_conf, buy_zone, buy_score_result,
            buy_sweep, buy_setup, pd_location,
            h4_bias, daily_bias, h1_conf["status"],
            regime, current_price, capital,
            reasons, warnings, data_quality,
            model_version=_buy_model_version,
        )
        result.daily_atr_pct = daily_atr_pct
        result.long_score = prefilter.long_score
        result.short_score = prefilter.short_score
        result.tf_scores = prefilter.tf_scores
        return result

    if sell_conf > buy_conf and sell_conf >= min_conf and sell_zone:
        result = _build_sell_signal(
            symbol, sell_conf, sell_zone, sell_score_result,
            sell_sweep, sell_setup, pd_location,
            h4_bias, daily_bias, h1_conf["status"],
            regime, current_price, capital,
            reasons, warnings, data_quality,
            model_version=_sell_model_version,
        )
        result.daily_atr_pct = daily_atr_pct
        result.long_score = prefilter.long_score
        result.short_score = prefilter.short_score
        result.tf_scores = prefilter.tf_scores
        return result

    return _hold(symbol, "No qualifying setup found", data_quality, reasons, warnings, regime)


# ── BUY evaluation ───────────────────────────────────────────────────────────

def _evaluate_buy(
    current_price, df_30m, df_1h,
    demand_zones, liq_levels, rng,
    h4_bias, daily_bias, h1_conf,
    reasons, min_zone_score,
):
    cfg = get_settings()
    best_zone = None
    best_score_result = {}
    best_conf = 0.0
    swept = False
    setup = ""

    for zone in demand_zones:
        # Buffer 2% — catches price approaching the top of a demand zone.
        if not price_in_zone(current_price, zone, buffer_pct=0.02):
            continue

        near_swing = any(
            abs(lvl["price"] - zone["zone_bottom"]) / (zone["zone_bottom"] + 0.001) < 0.015
            for lvl in liq_levels if "low" in lvl["level_type"]
        )
        liq_swept = any(
            lvl.get("swept") and "low" in lvl["level_type"]
            for lvl in liq_levels
        )

        # Score the zone — this is the authoritative quality gate
        sr = score_zone(zone, df_30m, h4_bias, daily_bias, liq_swept, near_swing,
                        source_tf=zone.get("source_tf", "30m"))
        zone["score"] = sr["score"]
        zone["rating"] = sr["rating"]

        if sr["score"] < min_zone_score:
            continue
        if sr["rating"] == "ignore":
            continue

        conf = _buy_confidence(h4_bias, daily_bias, h1_conf, sr, liq_swept, rng, df_30m)

        if conf > best_conf:
            best_conf = conf
            best_zone = zone
            best_score_result = sr
            swept = liq_swept
            setup = _detect_setup_type_buy(df_30m, liq_levels, zone)
            reasons.append(f"[30M] Demand zone {zone['zone_bottom']:.2f}–{zone['zone_top']:.2f} score={sr['score']:.0f} ({sr['rating']})")

    return best_conf, best_zone, best_score_result, swept, setup


def _buy_confidence(h4_bias, daily_bias, h1_conf, sr, liq_swept, rng, df_30m=None) -> float:
    conf = 0.0

    # 4H trend bias (0–20) — core directional filter
    if "strongly_bullish" in h4_bias:
        conf += 20
    elif "bullish" in h4_bias:
        conf += 15
    elif "neutral" in h4_bias:
        conf += 7
    # bearish h4 → 0 (we should not be buying in bearish 4H trend)

    # 1H confirmation (0–12) — lower cap, neutral still adds value
    if h1_conf["status"] == "bullish":
        conf += 12
    elif h1_conf["status"] == "neutral":
        conf += 6
    # bearish h1 → 0

    # 30M zone score (0–20) — quality of the zone
    conf += sr["score"] * 0.20

    # Liquidity sweep (0–12) — strong bonus but no longer required
    if liq_swept:
        conf += 12

    # Premium/discount location (0–10)
    loc = rng["location"]
    if "discount" in loc:
        conf += 10
    elif "equilibrium" in loc:
        conf += 5
    # premium → 0 (don't buy at premium)

    # RR quality (0–10)
    rr = sr.get("rr_ratio", 0)
    if rr >= 3:
        conf += 10
    elif rr >= 2:
        conf += 7
    elif rr >= 1.8:
        conf += 4
    elif rr >= 1.5:
        conf += 2

    # Daily bias alignment (0–8) — reduced weight, still provides directional filter
    if "bullish" in daily_bias:
        conf += 8
    elif "neutral" in daily_bias:
        conf += 4

    # RSI confirmation bonus (0–6) — oversold = higher quality buy
    if df_30m is not None and "rsi_14" in df_30m.columns:
        try:
            rsi_val = float(df_30m["rsi_14"].iloc[-1])
            if rsi_val < 30:
                conf += 6   # strongly oversold
            elif rsi_val < 40:
                conf += 4   # oversold
            elif rsi_val < 50:
                conf += 2   # approaching oversold
        except Exception:
            pass

    # Volume confirmation bonus (0–4) — high volume at zone = institutional interest
    if df_30m is not None and "vol_ma_20" in df_30m.columns:
        try:
            last_vol = float(df_30m["volume"].iloc[-1])
            vol_ma   = float(df_30m["vol_ma_20"].iloc[-1])
            if vol_ma > 0:
                vol_ratio = last_vol / vol_ma
                if vol_ratio >= 2.0:
                    conf += 4
                elif vol_ratio >= 1.5:
                    conf += 2
        except Exception:
            pass

    return round(min(100, conf), 1)


# ── SELL evaluation ───────────────────────────────────────────────────────────

def _evaluate_sell(
    current_price, df_30m, df_1h,
    supply_zones, liq_levels, rng,
    h4_bias, daily_bias, h1_conf,
    reasons, min_zone_score,
):
    cfg = get_settings()
    best_zone = None
    best_score_result = {}
    best_conf = 0.0
    swept = False
    setup = ""

    for zone in supply_zones:
        # Buffer 2% — catches price approaching the bottom of a supply zone.
        if not price_in_zone(current_price, zone, buffer_pct=0.02):
            continue

        near_swing = any(
            abs(lvl["price"] - zone["zone_top"]) / (zone["zone_top"] + 0.001) < 0.015
            for lvl in liq_levels if "high" in lvl["level_type"]
        )
        liq_swept = any(
            lvl.get("swept") and "high" in lvl["level_type"]
            for lvl in liq_levels
        )

        sr = score_zone(zone, df_30m, h4_bias, daily_bias, liq_swept, near_swing,
                        source_tf=zone.get("source_tf", "30m"))
        zone["score"] = sr["score"]
        zone["rating"] = sr["rating"]

        if sr["score"] < min_zone_score:
            continue
        if sr["rating"] == "ignore":
            continue

        conf = _sell_confidence(h4_bias, daily_bias, h1_conf, sr, liq_swept, rng, df_30m)

        if conf > best_conf:
            best_conf = conf
            best_zone = zone
            best_score_result = sr
            swept = liq_swept
            setup = _detect_setup_type_sell(df_30m, liq_levels, zone)
            reasons.append(f"[30M] Supply zone {zone['zone_bottom']:.2f}–{zone['zone_top']:.2f} score={sr['score']:.0f} ({sr['rating']})")

    return best_conf, best_zone, best_score_result, swept, setup


def _sell_confidence(h4_bias, daily_bias, h1_conf, sr, liq_swept, rng, df_30m=None) -> float:
    conf = 0.0
    if "strongly_bearish" in h4_bias:
        conf += 20
    elif "bearish" in h4_bias:
        conf += 15
    elif "neutral" in h4_bias:
        conf += 7

    if h1_conf["status"] == "bearish":
        conf += 12
    elif h1_conf["status"] == "neutral":
        conf += 6

    conf += sr["score"] * 0.20

    if liq_swept:
        conf += 12

    loc = rng["location"]
    if "premium" in loc:
        conf += 10
    elif "equilibrium" in loc:
        conf += 5

    rr = sr.get("rr_ratio", 0)
    if rr >= 3:
        conf += 10
    elif rr >= 2:
        conf += 7
    elif rr >= 1.8:
        conf += 4
    elif rr >= 1.5:
        conf += 2

    # Daily bias alignment
    if "bearish" in daily_bias:
        conf += 8
    elif "neutral" in daily_bias:
        conf += 4

    # RSI confirmation bonus (0–6) — overbought = higher quality sell
    if df_30m is not None and "rsi_14" in df_30m.columns:
        try:
            rsi_val = float(df_30m["rsi_14"].iloc[-1])
            if rsi_val > 70:
                conf += 6   # strongly overbought
            elif rsi_val > 60:
                conf += 4   # overbought
            elif rsi_val > 50:
                conf += 2   # approaching overbought
        except Exception:
            pass

    # Volume confirmation bonus (0–4)
    if df_30m is not None and "vol_ma_20" in df_30m.columns:
        try:
            last_vol = float(df_30m["volume"].iloc[-1])
            vol_ma   = float(df_30m["vol_ma_20"].iloc[-1])
            if vol_ma > 0:
                vol_ratio = last_vol / vol_ma
                if vol_ratio >= 2.0:
                    conf += 4
                elif vol_ratio >= 1.5:
                    conf += 2
        except Exception:
            pass

    return round(min(100, conf), 1)


# ── Setup type detection ──────────────────────────────────────────────────────

def _detect_setup_type_buy(df_30m, liq_levels, zone) -> str:
    # Trap-and-Reclaim: recent sweep of a low, quick reclaim
    for lvl in liq_levels:
        if "low" in lvl["level_type"] and lvl.get("swept"):
            return "trap_and_reclaim"

    # Pullback to demand with bullish structure
    return "pullback_demand"


def _detect_setup_type_sell(df_30m, liq_levels, zone) -> str:
    for lvl in liq_levels:
        if "high" in lvl["level_type"] and lvl.get("swept"):
            return "trap_and_reclaim"
    return "pullback_supply"


# ── Signal builders ───────────────────────────────────────────────────────────

def _build_buy_signal(
    symbol, conf, zone, sr, swept, setup, pd_location,
    h4_bias, daily_bias, h1_status,
    regime, current_price, capital,
    reasons, warnings, data_quality,
    model_version: str = ENGINE_VERSION,
    simplified: bool = False,
) -> SignalResult:
    entry = sr.get("entry", zone_midpoint(zone))
    sl    = sr.get("sl", zone["zone_bottom"] * 0.999)
    tp    = sr.get("tp", 0.0)
    rr    = sr.get("rr_ratio", risk_reward(entry, sl, tp))

    cfg = get_settings()
    min_rr = cfg.simple_min_rr_ratio if simplified else cfg.min_rr_ratio
    if rr < min_rr:
        warnings.append(f"R:R {rr:.1f} below minimum {min_rr:.1f}")
        return _hold(symbol, f"R:R {rr:.1f} too low for BUY", data_quality, reasons, warnings, regime)

    if not simplified and pd_location == "premium":
        warnings.append("BUY blocked: price at premium — waiting for discount/equilibrium pullback")
        return _hold(symbol, "BUY at premium zone", data_quality, reasons, warnings, regime)

    if not simplified and h1_status == "bearish":
        warnings.append("BUY blocked: 1H structure is actively bearish — directional conflict")
        return _hold(symbol, "1H bearish opposes BUY", data_quality, reasons, warnings, regime)

    reasons.append(f"BUY confidence {conf:.0f}/100")
    reasons.append(f"Setup: {_SETUP_TYPES.get(setup, setup)}")

    return SignalResult(
        symbol=symbol,
        signal="BUY",
        confidence=conf,
        market_regime=regime,
        daily_bias=daily_bias,
        h4_bias=h4_bias,
        h1_confirmation=h1_status,
        m30_zone_type="demand",
        zone_score=zone.get("score", 0),
        zone_rating=zone.get("rating", ""),
        liquidity_sweep=swept,
        premium_discount=pd_location,
        entry_price=round(entry, 4),
        stop_loss=round(sl, 4),
        take_profit=round(tp, 4),
        risk_reward=round(rr, 2),
        setup_type=setup,
        reasons=reasons,
        warnings=warnings,
        data_quality=data_quality,
        model_version=model_version,
    )


def _build_sell_signal(
    symbol, conf, zone, sr, swept, setup, pd_location,
    h4_bias, daily_bias, h1_status,
    regime, current_price, capital,
    reasons, warnings, data_quality,
    model_version: str = ENGINE_VERSION,
    simplified: bool = False,
) -> SignalResult:
    entry = sr.get("entry", zone_midpoint(zone))
    sl    = sr.get("sl", zone["zone_top"] * 1.001)
    tp    = sr.get("tp", 0.0)
    rr    = sr.get("rr_ratio", risk_reward(entry, sl, tp))

    cfg = get_settings()
    min_rr = cfg.simple_min_rr_ratio if simplified else cfg.min_rr_ratio
    if rr < min_rr:
        warnings.append(f"R:R {rr:.1f} below minimum {min_rr:.1f}")
        return _hold(symbol, f"R:R {rr:.1f} too low for SELL", data_quality, reasons, warnings, regime)

    if not simplified and pd_location in ("discount", "deep_discount"):
        warnings.append(f"SELL blocked: price at {pd_location} — waiting for premium/equilibrium")
        return _hold(symbol, f"SELL at {pd_location} zone", data_quality, reasons, warnings, regime)

    if not simplified and h1_status == "bullish":
        warnings.append("SELL blocked: 1H structure is actively bullish — directional conflict")
        return _hold(symbol, "1H bullish opposes SELL", data_quality, reasons, warnings, regime)

    reasons.append(f"SELL confidence {conf:.0f}/100")
    reasons.append(f"Setup: {_SETUP_TYPES.get(setup, setup)}")

    return SignalResult(
        symbol=symbol,
        signal="SELL",
        confidence=conf,
        market_regime=regime,
        daily_bias=daily_bias,
        h4_bias=h4_bias,
        h1_confirmation=h1_status,
        m30_zone_type="supply",
        zone_score=zone.get("score", 0),
        zone_rating=zone.get("rating", ""),
        liquidity_sweep=swept,
        premium_discount=pd_location,
        entry_price=round(entry, 4),
        stop_loss=round(sl, 4),
        take_profit=round(tp, 4),
        risk_reward=round(rr, 2),
        setup_type=setup,
        reasons=reasons,
        warnings=warnings,
        data_quality=data_quality,
        model_version=model_version,
    )


# ── 1H confirmation helper ────────────────────────────────────────────────────

def _h1_confirmation(h1_struct, h1_trend, df_1h) -> dict:
    bias = h1_trend.get("bias", "neutral")
    structure = h1_struct.get("structure", "undefined")
    bos = h1_struct.get("last_bos")
    details = list(h1_trend.get("details", []))

    if is_bullish_bias(bias) and (structure == "bullish" or bos == "bullish"):
        status = "bullish"
        details.append("1H confirms bullish bias")
    elif is_bearish_bias(bias) and (structure == "bearish" or bos == "bearish"):
        status = "bearish"
        details.append("1H confirms bearish bias")
    else:
        status = "neutral"
        details.append(f"1H inconclusive — bias={bias}, structure={structure}")

    return {"status": status, "details": details}


# ── Model helpers ─────────────────────────────────────────────────────────────

def _model_blend_factor(regime: str) -> float:
    if regime in ("distribution", "bearish_trend", "high_volatility_liquidation"):
        return 25.0
    elif regime in ("bullish_trend", "accumulation"):
        return 18.0
    else:
        return 20.0


def _build_model_features(
    h4_bias: str,
    h1_conf_status: str,
    sr: dict,
    liq_swept: bool,
    rng: dict,
    df_30m: "pd.DataFrame",
    regime: str,
    model_type: str,
    *,
    df_1d: "pd.DataFrame | None" = None,
) -> dict:
    """Convert signal components into the feature dict expected by the ML models."""
    _bias = {"strongly_bullish": 2.0, "bullish": 1.0, "neutral": 0.0,
             "bearish": -1.0, "strongly_bearish": -2.0}
    _conf = {"bullish": 1.0, "neutral": 0.0, "bearish": -1.0}
    _pd_buy  = {"deep_discount": 2.0, "discount": 1.0, "equilibrium": 0.0, "premium": -1.0}
    _pd_sell = {"premium": 2.0, "equilibrium": 0.0, "discount": -1.0, "deep_discount": -2.0}
    _reg_buy  = {"bullish_trend": 2.0, "accumulation": 1.0, "trap_zone": 0.5}
    _reg_sell = {"bearish_trend": 2.0, "distribution": 1.0, "trap_zone": 0.5}

    vol_ratio = 1.0
    if not df_30m.empty and "volume" in df_30m.columns and "vol_ma_20" in df_30m.columns:
        try:
            vol = float(df_30m["volume"].iloc[-1])
            vma = float(df_30m["vol_ma_20"].iloc[-1])
            if vma > 0:
                vol_ratio = round(vol / vma, 4)
        except Exception:
            pass

    def _safe(col: str, default: float) -> float:
        if df_30m.empty or col not in df_30m.columns:
            return default
        try:
            return float(df_30m[col].iloc[-1])
        except Exception:
            return default

    _pd  = _pd_buy  if model_type == "buy" else _pd_sell
    _reg = _reg_buy if model_type == "buy" else _reg_sell
    return {
        "h4_bias_score":     _bias.get(h4_bias, 0.0),
        "h1_conf_score":     _conf.get(h1_conf_status, 0.0),
        "zone_score":        float(sr.get("score", 50)),
        "liq_sweep":         int(liq_swept),
        "pd_location_score": _pd.get(rng.get("location", "equilibrium"), 0.0),
        "rr_ratio":          float(sr.get("rr_ratio", 2.0)),
        "volume_ratio":      vol_ratio,
        "rsi_14":            _safe("rsi_14", 50.0),
        "body_atr_ratio":    _safe("body_atr_ratio", 1.0),
        "above_ema50":       int(_safe("above_ema50", 0.0)),
        "above_ema200":      int(_safe("above_ema200", 0.0)),
        "regime_score":      _reg.get(str(regime).lower(), 0.0),
        "atr_expansion":     _safe("atr_expansion", 1.0),
        "ema_alignment":     _safe("ema_alignment", 1.5),
        "price_velocity":    _safe("price_velocity", 0.0),
        # Raw independent features — not derived from the rule engine
        "roc_10":            _roc(df_30m, 10),
        "roc_20":            _roc(df_30m, 20),
        "vol_trend":         _vol_trend(df_30m),
        "htf_rsi_daily":     _safe_daily_rsi(df_1d),
    }


# ── Raw feature helpers (independent of rule engine) ─────────────────────────

def _roc(df: pd.DataFrame, period: int) -> float:
    """Rate of change over `period` bars, normalized by ATR. Clipped to [-5, 5]."""
    if df.empty or len(df) < period + 1 or "atr_14" not in df.columns:
        return 0.0
    try:
        c_now = float(df["close"].iloc[-1])
        c_n   = float(df["close"].iloc[-(period + 1)])
        atr   = float(df["atr_14"].iloc[-1])
        return float(max(-5.0, min(5.0, (c_now - c_n) / atr))) if atr > 0 else 0.0
    except Exception:
        return 0.0


def _vol_trend(df: pd.DataFrame, window: int = 10) -> float:
    """Linear slope of volume over last `window` bars, normalized to avg volume. Range [-1, 1]."""
    if df.empty or len(df) < window or "volume" not in df.columns:
        return 0.0
    try:
        vols = df["volume"].iloc[-window:].values.astype(float)
        avg_vol = float(np.mean(vols))
        if avg_vol <= 0:
            return 0.0
        slope = float(np.polyfit(np.arange(len(vols)), vols, 1)[0])
        return float(max(-1.0, min(1.0, slope / avg_vol)))
    except Exception:
        return 0.0


def _safe_daily_rsi(df_1d: "pd.DataFrame | None") -> float:
    """RSI-14 from the daily timeframe at the time of the signal. Returns 50.0 if unavailable."""
    if df_1d is None or df_1d.empty or "rsi_14" not in df_1d.columns:
        return 50.0
    try:
        return float(df_1d["rsi_14"].iloc[-1])
    except Exception:
        return 50.0


# ── Option B: simplified 4-rule signal path ───────────────────────────────────

_SIMPLE_RATINGS = {"A", "A+"}


def _generate_simple_signal(
    symbol: str,
    df_4h: pd.DataFrame,
    df_1h: pd.DataFrame,
    df_30m: pd.DataFrame,
    capital: float,
    data_quality: str,
    daily_atr_pct: float,
) -> SignalResult:
    """
    Four rules only:
      1. 4H trend defines direction (no counter-trend)
      2. A/A+ zone within 1% of price
      3. Rejection candle enforced in SignalFilter
      4. R:R >= simple_min_rr_ratio (default 2.0)
    """
    cfg = get_settings_for_symbol(symbol)
    reasons: list[str] = ["[Simple] v6 four-rule path"]
    warnings: list[str] = []
    current_price = float(df_30m["close"].iloc[-1])

    h4_trend = detect_trend_bias(df_4h) if not df_4h.empty else {"bias": "neutral", "details": []}
    h4_bias = h4_trend["bias"]
    reasons.extend([f"[4H] {d}" for d in h4_trend.get("details", [])])

    if is_bullish_bias(h4_bias):
        direction = "LONG"
    elif is_bearish_bias(h4_bias):
        direction = "SHORT"
    else:
        return _hold(symbol, f"4H trend neutral ({h4_bias}) — no direction", data_quality, reasons, warnings)

    h1_struct = detect_structure(df_1h) if not df_1h.empty else {"structure": "undefined", "details": []}
    h1_trend = detect_trend_bias(df_1h) if not df_1h.empty else {"bias": "neutral", "details": []}
    h1_conf = _h1_confirmation(h1_struct, h1_trend, df_1h)

    zones_raw = (
        detect_zones(df_30m, lookback=300, timeframe="30m")
        + detect_zones(df_4h, lookback=200, timeframe="4h")
    )
    liq_levels = detect_liquidity_levels(df_30m)
    liq_levels = detect_sweeps(df_30m, liq_levels)
    rng = get_range(df_30m, lookback=100)
    pd_location = rng["location"]
    regime = "simple_4h_trend"
    buf = cfg.simple_zone_buffer_pct

    if direction == "LONG":
        buy_conf, buy_zone, buy_score_result, buy_sweep, buy_setup = _evaluate_simple_buy(
            current_price, df_30m, [z for z in zones_raw if z["zone_type"] == "demand"],
            liq_levels, h4_bias, reasons, buf,
        )
        if not buy_zone:
            return _hold(symbol, "No A/A+ demand zone within 1% of price", data_quality, reasons, warnings, regime)
        result = _build_buy_signal(
            symbol, buy_conf, buy_zone, buy_score_result,
            buy_sweep, buy_setup, pd_location,
            h4_bias, h4_bias, h1_conf["status"],
            regime, current_price, capital,
            reasons, warnings, data_quality,
            simplified=True,
        )
        result.daily_atr_pct = daily_atr_pct
        return result

    sell_conf, sell_zone, sell_score_result, sell_sweep, sell_setup = _evaluate_simple_sell(
        current_price, df_30m, [z for z in zones_raw if z["zone_type"] == "supply"],
        liq_levels, h4_bias, reasons, buf,
    )
    if not sell_zone:
        return _hold(symbol, "No A/A+ supply zone within 1% of price", data_quality, reasons, warnings, regime)
    result = _build_sell_signal(
        symbol, sell_conf, sell_zone, sell_score_result,
        sell_sweep, sell_setup, pd_location,
        h4_bias, h4_bias, h1_conf["status"],
        regime, current_price, capital,
        reasons, warnings, data_quality,
        simplified=True,
    )
    result.daily_atr_pct = daily_atr_pct
    return result


def _evaluate_simple_buy(
    current_price, df_30m, demand_zones, liq_levels, h4_bias, reasons, buffer_pct,
):
    best_zone = None
    best_score_result = {}
    best_conf = 0.0
    swept = False
    setup = ""

    for zone in demand_zones:
        if not price_in_zone(current_price, zone, buffer_pct=buffer_pct):
            continue
        liq_swept = any(
            lvl.get("swept") and "low" in lvl["level_type"]
            for lvl in liq_levels
        )
        sr = score_zone(zone, df_30m, h4_bias, h4_bias, liq_swept, False,
                        source_tf=zone.get("source_tf", "30m"))
        zone["score"] = sr["score"]
        zone["rating"] = sr["rating"]
        if sr["rating"] not in _SIMPLE_RATINGS:
            continue
        conf = float(sr["score"])
        if conf > best_conf:
            best_conf = conf
            best_zone = zone
            best_score_result = sr
            swept = liq_swept
            setup = _detect_setup_type_buy(df_30m, liq_levels, zone)
            reasons.append(
                f"[Simple] Demand {zone['zone_bottom']:.2f}–{zone['zone_top']:.2f} "
                f"score={sr['score']:.0f} ({sr['rating']})"
            )
    return best_conf, best_zone, best_score_result, swept, setup


def _evaluate_simple_sell(
    current_price, df_30m, supply_zones, liq_levels, h4_bias, reasons, buffer_pct,
):
    best_zone = None
    best_score_result = {}
    best_conf = 0.0
    swept = False
    setup = ""

    for zone in supply_zones:
        if not price_in_zone(current_price, zone, buffer_pct=buffer_pct):
            continue
        liq_swept = any(
            lvl.get("swept") and "high" in lvl["level_type"]
            for lvl in liq_levels
        )
        sr = score_zone(zone, df_30m, h4_bias, h4_bias, liq_swept, False,
                        source_tf=zone.get("source_tf", "30m"))
        zone["score"] = sr["score"]
        zone["rating"] = sr["rating"]
        if sr["rating"] not in _SIMPLE_RATINGS:
            continue
        conf = float(sr["score"])
        if conf > best_conf:
            best_conf = conf
            best_zone = zone
            best_score_result = sr
            swept = liq_swept
            setup = _detect_setup_type_sell(df_30m, liq_levels, zone)
            reasons.append(
                f"[Simple] Supply {zone['zone_bottom']:.2f}–{zone['zone_top']:.2f} "
                f"score={sr['score']:.0f} ({sr['rating']})"
            )
    return best_conf, best_zone, best_score_result, swept, setup


# ── HOLD factory ──────────────────────────────────────────────────────────────

def _hold(
    symbol: str,
    reason: str,
    data_quality: str,
    reasons: list | None = None,
    warnings: list | None = None,
    regime: str = "",
) -> SignalResult:
    r = list(reasons or [])
    w = list(warnings or [])
    w.append(f"HOLD reason: {reason}")
    return SignalResult(
        symbol=symbol,
        signal="HOLD",
        confidence=0.0,
        market_regime=regime,
        reasons=r,
        warnings=w,
        data_quality=data_quality,
        rejection_reason=reason,
    )


# ── BLOCKED factory ───────────────────────────────────────────────────────────

def _blocked(
    symbol: str,
    prefilter,           # PrefilterResult
    data_quality: str,
    reasons: list | None = None,
    warnings: list | None = None,
    regime: str = "",
) -> SignalResult:
    w = list(warnings or [])
    w.append(f"BLOCKED: {prefilter.reason}")
    return SignalResult(
        symbol=symbol,
        signal="BLOCKED",
        confidence=0.0,
        market_regime=regime,
        long_score=prefilter.long_score,
        short_score=prefilter.short_score,
        tf_scores=prefilter.tf_scores,
        rejection_reason=prefilter.reason,
        reasons=list(reasons or []),
        warnings=w,
        data_quality=data_quality,
    )


