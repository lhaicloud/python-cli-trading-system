"""
Candle-level label generator for independent ML training data.

Labels each candle with a directional outcome based on forward price action,
producing training samples that are independent of the rule-based signal engine.
This avoids the circular feedback problem where the ML model is trained on
features derived from the same rules it's supposed to improve.

Labels:
     1  — bullish: price rose by atr_multiple × ATR before falling by same amount
    -1  — bearish: price fell by atr_multiple × ATR before rising by same amount
     0  — inconclusive: neither threshold hit in lookahead window (excluded from training)
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.data.repository import get_candles
from app.ta.indicators import add_indicators
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Raw features used by candle-based models (no rule-engine-derived scores)
CANDLE_FEATURE_COLS = [
    "rsi_14",
    "volume_ratio",
    "atr_expansion",
    "ema_alignment",
    "price_velocity",
    "body_atr_ratio",
    "above_ema50",
    "above_ema200",
    "roc_10",
    "roc_20",
    "vol_trend",
]


def label_candles(
    symbol: str,
    timeframe: str = "30m",
    lookahead: int = 20,
    atr_multiple: float = 1.5,
) -> pd.DataFrame:
    """
    Generate labeled training data from raw candles.

    For each candle, scans the next `lookahead` candles to determine if price
    made a clean directional move of `atr_multiple × ATR` before reversing.

    Returns a DataFrame with columns matching CANDLE_FEATURE_COLS + "label".
    Inconclusive candles (label=0) are included — callers decide whether to drop them.
    """
    df = get_candles(symbol, timeframe)
    if df.empty:
        logger.warning("[candle_labeler] No %s data for %s", timeframe, symbol)
        return pd.DataFrame()

    df = add_indicators(df)
    min_rows = lookahead + 50
    if len(df) < min_rows:
        logger.warning(
            "[candle_labeler] Only %d candles for %s (need %d)", len(df), symbol, min_rows
        )
        return pd.DataFrame()

    rows: list[dict] = []
    for i in range(50, len(df) - lookahead):
        row   = df.iloc[i]
        atr   = float(row.get("atr_14", 0)) or float(row["close"]) * 0.005
        close = float(row["close"])

        up_target   = close + atr * atr_multiple
        down_target = close - atr * atr_multiple

        # Scan forward to find which target is hit first
        future  = df.iloc[i + 1 : i + 1 + lookahead]
        hit_up  = False
        hit_dn  = False
        for _, frow in future.iterrows():
            h = float(frow["high"])
            lo = float(frow["low"])
            if not hit_dn and h >= up_target:
                hit_up = True
                break
            if not hit_up and lo <= down_target:
                hit_dn = True
                break

        if hit_up and not hit_dn:
            label = 1
        elif hit_dn and not hit_up:
            label = -1
        else:
            label = 0

        feat = _extract_features(df, i, row, close, atr)
        feat["label"] = label
        rows.append(feat)

    if not rows:
        return pd.DataFrame()

    result = pd.DataFrame(rows)
    logger.info(
        "[candle_labeler] %s %s: %d candles labeled "
        "(bull=%d bear=%d inconclusive=%d)",
        symbol, timeframe, len(result),
        int((result["label"] == 1).sum()),
        int((result["label"] == -1).sum()),
        int((result["label"] == 0).sum()),
    )
    return result


# ── Feature extraction ────────────────────────────────────────────────────────

def _extract_features(
    df: pd.DataFrame,
    i: int,
    row: pd.Series,
    close: float,
    atr: float,
) -> dict:
    """Compute raw features for candle at position i."""
    try:
        # Volume ratio (current / 20-bar MA)
        vol_ma = float(row.get("vol_ma_20", 0)) or 1.0
        vol_ratio = round(float(row.get("volume", 0)) / vol_ma, 4)

        # ATR expansion vs 50-bar rolling average
        atr_series = df["atr_14"].iloc[max(0, i - 50) : i]
        atr_avg = float(atr_series.mean()) if len(atr_series) > 0 else atr
        atr_expansion = round(atr / atr_avg, 4) if atr_avg > 0 else 1.0

        # EMA alignment count (0–3: bullish stacking)
        e9   = float(row.get("ema_9",   close))
        e21  = float(row.get("ema_21",  close))
        e50  = float(row.get("ema_50",  close))
        e200 = float(row.get("ema_200", close))
        ema_alignment = float(int(e9 > e21) + int(e21 > e50) + int(e50 > e200))

        # 3-bar price velocity normalized by ATR
        price_velocity = 0.0
        if i >= 4 and atr > 0:
            c3 = float(df["close"].iloc[i - 3])
            price_velocity = round(max(-5.0, min(5.0, (close - c3) / atr)), 4)

        # Body/ATR ratio
        body = abs(float(row.get("body", close - float(row["open"]))))
        body_atr_ratio = round(body / atr, 4) if atr > 0 else 1.0

        # Rate of change: (close_now - close_N) / ATR, clipped [-5, 5]
        def _roc(period: int) -> float:
            if i < period or atr <= 0:
                return 0.0
            c_n = float(df["close"].iloc[i - period])
            return round(max(-5.0, min(5.0, (close - c_n) / atr)), 4)

        # Volume trend: normalized linear slope over last 10 bars
        vol_trend = 0.0
        vols = df["volume"].iloc[max(0, i - 10) : i].values.astype(float)
        if len(vols) >= 3:
            avg_v = float(np.mean(vols))
            if avg_v > 0:
                slope = float(np.polyfit(np.arange(len(vols)), vols, 1)[0])
                vol_trend = round(max(-1.0, min(1.0, slope / avg_v)), 4)

        return {
            "rsi_14":        float(row.get("rsi_14", 50.0)),
            "volume_ratio":  vol_ratio,
            "atr_expansion": atr_expansion,
            "ema_alignment": ema_alignment,
            "price_velocity": price_velocity,
            "body_atr_ratio": body_atr_ratio,
            "above_ema50":   int(close > e50),
            "above_ema200":  int(close > e200),
            "roc_10":        _roc(10),
            "roc_20":        _roc(20),
            "vol_trend":     vol_trend,
        }
    except Exception as exc:
        logger.debug("[candle_labeler] Feature extraction failed at i=%d: %s", i, exc)
        return {col: 0.0 for col in CANDLE_FEATURE_COLS}
