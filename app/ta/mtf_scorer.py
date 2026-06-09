"""
Multi-Timeframe Scoring Engine — rejection-first pre-filter.

Scores each timeframe's bull/bear strength independently, combines via
configurable weights, then gates direction and runs entry filters before
the zone engine is ever invoked.

Decision flow:
  1. Score each TF (1D, 12H, 4H, 1H, 30M) → bull/bear 0–100
  2. Compute weighted long_score / short_score
  3. 1W regime gate (blocks extremes only)
  4. Conviction check (max score < threshold → HOLD)
  5. Conflict check (gap too small → BLOCKED)
  6. Direction determined (higher score wins)
  7. Entry filters: RSI, EMA200, volume
  8. Return PrefilterResult(decision, direction, scores, reason)
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from app.ta.trend import detect_trend_bias, TrendBias
from app.utils.logger import get_logger

logger = get_logger(__name__)


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class TfScore:
    timeframe:  str
    bull_score: float   # 0–100
    bear_score: float   # 0–100
    weight:     float


@dataclass
class PrefilterResult:
    decision:    str              # "LONG" | "SHORT" | "HOLD" | "BLOCKED"
    direction:   str | None       # "LONG" or "SHORT" if passed; None if HOLD/BLOCKED
    long_score:  float
    short_score: float
    score_gap:   float
    tf_scores:   list = field(default_factory=list)   # list[TfScore]
    reason:      str = ""


# ── Per-TF scoring ────────────────────────────────────────────────────────────

def score_timeframe(df: pd.DataFrame, timeframe: str, weight: float) -> TfScore:
    """
    Compute bull/bear scores (0–100 each) for a single indicator-enriched DataFrame.

    Components (max 100 pts per direction):
      EMA alignment  (ema_alignment 0–3)   → 30 pts
      Price vs EMA200 (above_ema200 bool)  → 20 pts
      RSI position   (rsi_14)              → 25 pts
      Price velocity (price_velocity ±5)   → 15 pts
      Volume confirm (volume / vol_ma_20)  → 10 pts
    """
    if df.empty or len(df) < 5:
        return TfScore(timeframe=timeframe, bull_score=50.0, bear_score=50.0, weight=weight)

    try:
        last = df.iloc[-1]

        ema_align    = float(last.get("ema_alignment", 1.5))
        above_ema200 = bool(last.get("above_ema200", False))
        rsi          = float(last.get("rsi_14", 50.0))
        velocity     = float(last.get("price_velocity", 0.0))

        # Volume ratio — compute inline (column may not be pre-stored as ratio)
        raw_vol  = float(last.get("volume", 0.0))
        vol_ma   = float(last.get("vol_ma_20", 0.0))
        vol_ratio = (raw_vol / vol_ma) if vol_ma > 0 else 1.0

        # ── Bull score ────────────────────────────────────────────────────────
        bull = 0.0
        bull += (ema_align / 3.0) * 30.0                        # EMA alignment
        bull += 20.0 if above_ema200 else 0.0                   # EMA200 position
        bull += max(0.0, (rsi - 50.0) / 50.0) * 25.0            # RSI above midpoint
        bull += max(0.0, velocity / 5.0) * 15.0                  # positive velocity
        if vol_ratio > 1.5 and velocity > 0:                     # strong volume confirms bull
            bull += 10.0
        elif vol_ratio > 1.2 and velocity > 0:                   # moderate volume confirms bull
            bull += 5.0

        # ── Bear score ────────────────────────────────────────────────────────
        bear = 0.0
        bear += ((3.0 - ema_align) / 3.0) * 30.0               # inverse EMA alignment
        bear += 20.0 if not above_ema200 else 0.0               # below EMA200
        bear += max(0.0, (50.0 - rsi) / 50.0) * 25.0            # RSI below midpoint
        bear += max(0.0, -velocity / 5.0) * 15.0                 # negative velocity
        if vol_ratio > 1.5 and velocity < 0:                     # strong volume confirms bear
            bear += 10.0
        elif vol_ratio > 1.2 and velocity < 0:                   # moderate volume confirms bear
            bear += 5.0

        return TfScore(
            timeframe=timeframe,
            bull_score=round(min(100.0, bull), 1),
            bear_score=round(min(100.0, bear), 1),
            weight=weight,
        )

    except Exception as exc:
        logger.debug("score_timeframe error on %s: %s", timeframe, exc)
        return TfScore(timeframe=timeframe, bull_score=50.0, bear_score=50.0, weight=weight)


# ── Main pre-filter ───────────────────────────────────────────────────────────

def run_prefilter(
    df_1w:  pd.DataFrame,
    df_1d:  pd.DataFrame,
    df_12h: pd.DataFrame,
    df_4h:  pd.DataFrame,
    df_1h:  pd.DataFrame,
    df_30m: pd.DataFrame,
    cfg,                       # app.config.Settings
) -> PrefilterResult:
    """
    Gate the signal before the zone engine runs.
    Returns PrefilterResult with decision LONG | SHORT | HOLD | BLOCKED.
    """
    weights = {
        "1w":  cfg.mtf_tf_weight_1w,
        "1d":  cfg.mtf_tf_weight_1d,
        "12h": cfg.mtf_tf_weight_12h,
        "4h":  cfg.mtf_tf_weight_4h,
        "1h":  cfg.mtf_tf_weight_1h,
        "30m": cfg.mtf_tf_weight_30m,
    }

    # ── Step 1: Score each TF (including 1W in composite) ────────────────────
    scored_tfs = [
        (df_1w,  "1w"),
        (df_1d,  "1d"),
        (df_12h, "12h"),
        (df_4h,  "4h"),
        (df_1h,  "1h"),
        (df_30m, "30m"),
    ]
    tf_scores: list[TfScore] = [
        score_timeframe(df, tf, weights[tf]) for df, tf in scored_tfs
    ]

    # ── Step 2: Weighted final scores ─────────────────────────────────────────
    long_score  = sum(s.bull_score * s.weight for s in tf_scores)
    short_score = sum(s.bear_score * s.weight for s in tf_scores)
    long_score  = round(long_score, 1)
    short_score = round(short_score, 1)

    def _result(decision: str, reason: str, direction: str | None = None) -> PrefilterResult:
        gap = abs(long_score - short_score)
        return PrefilterResult(
            decision=decision,
            direction=direction,
            long_score=long_score,
            short_score=short_score,
            score_gap=round(gap, 1),
            tf_scores=tf_scores,
            reason=reason,
        )

    # ── Step 3: 1W double-confirmation gate ──────────────────────────────────
    # 1W is now included in the composite score so single-TF "strongly bearish"
    # is already penalising long_score. Only block when BOTH 1W and 1D confirm
    # the same direction — this filters genuine sustained macro trends while
    # allowing recoveries (where 1D turns bullish before 1W EMAs catch up).
    if not df_1w.empty and len(df_1w) >= 50:
        try:
            w1_bias = detect_trend_bias(df_1w)["bias"]
            tentative_dir = "LONG" if long_score >= short_score else "SHORT"

            # Block LONG only when BOTH weekly AND daily are bearish (macro downtrend)
            if w1_bias in (TrendBias.BEARISH, TrendBias.STRONGLY_BEARISH) and tentative_dir == "LONG":
                if not df_1d.empty and len(df_1d) >= 5:
                    d1_bias = detect_trend_bias(df_1d)["bias"]
                    if d1_bias in (TrendBias.BEARISH, TrendBias.STRONGLY_BEARISH):
                        return _result("BLOCKED", "1W+1D both bearish — longs blocked")

            # Block SHORT only when BOTH weekly AND daily are bullish (macro uptrend)
            if w1_bias in (TrendBias.BULLISH, TrendBias.STRONGLY_BULLISH) and tentative_dir == "SHORT":
                if not df_1d.empty and len(df_1d) >= 5:
                    d1_bias = detect_trend_bias(df_1d)["bias"]
                    if d1_bias in (TrendBias.BULLISH, TrendBias.STRONGLY_BULLISH):
                        return _result("BLOCKED", "1W+1D both bullish — shorts blocked")
        except Exception as exc:
            logger.debug("1W gate error: %s", exc)

    # ── Step 4: Conviction check ──────────────────────────────────────────────
    winning_score = max(long_score, short_score)
    if winning_score < cfg.mtf_min_score:
        return _result("HOLD", f"no conviction — best score {winning_score:.1f} < {cfg.mtf_min_score}")

    # ── Step 5: Conflict check ────────────────────────────────────────────────
    score_gap = winning_score - min(long_score, short_score)
    if score_gap < cfg.mtf_min_score_gap:
        return _result(
            "BLOCKED",
            f"bull/bear conflict — gap {score_gap:.1f} < {cfg.mtf_min_score_gap}",
        )

    # ── Step 6: Direction ─────────────────────────────────────────────────────
    direction = "LONG" if long_score >= short_score else "SHORT"

    # ── Step 7: Entry filters (use 30M as execution TF) ───────────────────────
    if not df_30m.empty and len(df_30m) >= 1:
        last_30m = df_30m.iloc[-1]

        # RSI filter
        rsi_30m = float(last_30m.get("rsi_14", 50.0))
        if direction == "LONG" and rsi_30m > cfg.mtf_rsi_overbought:
            return _result("BLOCKED", f"RSI overbought on 30M: {rsi_30m:.1f}")
        if direction == "SHORT" and rsi_30m < cfg.mtf_rsi_oversold:
            return _result("BLOCKED", f"RSI oversold on 30M: {rsi_30m:.1f}")

        # EMA200 filter
        above_ema200 = bool(last_30m.get("above_ema200", True))
        if cfg.mtf_ema200_strict:
            if direction == "LONG" and not above_ema200:
                return _result("BLOCKED", "price below EMA200 on 30M — strict mode")
            if direction == "SHORT" and above_ema200:
                return _result("BLOCKED", "price above EMA200 on 30M — strict mode")

        # Volume filter
        raw_vol = float(last_30m.get("volume", 0.0))
        vol_ma  = float(last_30m.get("vol_ma_20", 0.0))
        if vol_ma > 0:
            vol_ratio = raw_vol / vol_ma
            if vol_ratio < cfg.mtf_min_volume_ratio:
                return _result("BLOCKED", f"low volume on 30M: ratio {vol_ratio:.2f}")

    # ── Step 8: Pass ──────────────────────────────────────────────────────────
    return _result(direction, f"prefilter passed — {direction}", direction=direction)
