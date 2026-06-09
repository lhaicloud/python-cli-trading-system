"""Core technical indicators applied to a DataFrame of OHLCV candles."""

from __future__ import annotations

import pandas as pd

from app.utils.math_utils import atr, ema, rsi, sma


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute and attach all indicators to a candle DataFrame.

    Expected columns: open, high, low, close, volume
    Returns same DataFrame with additional columns.
    """
    df = df.copy()

    # EMAs
    df["ema_9"]   = ema(df["close"], 9)
    df["ema_21"]  = ema(df["close"], 21)
    df["ema_50"]  = ema(df["close"], 50)
    df["ema_200"] = ema(df["close"], 200)

    # SMAs
    df["sma_20"] = sma(df["close"], 20)

    # ATR
    df["atr_14"] = atr(df["high"], df["low"], df["close"], 14)
    df["atr_7"]  = atr(df["high"], df["low"], df["close"], 7)

    # RSI
    df["rsi_14"] = rsi(df["close"], 14)

    # Volume MA
    df["vol_ma_20"] = sma(df["volume"], 20)

    # Candle body and wick
    df["body"]      = (df["close"] - df["open"]).abs()
    df["upper_wick"] = df["high"] - df[["open", "close"]].max(axis=1)
    df["lower_wick"] = df[["open", "close"]].min(axis=1) - df["low"]
    df["is_bull"]   = (df["close"] > df["open"]).astype(int)

    # Body vs ATR ratio (displacement quality)
    df["body_atr_ratio"] = df["body"] / df["atr_14"].replace(0, float("nan"))

    # ATR expansion: current ATR vs its 50-bar rolling mean (volatility regime)
    atr_roll = df["atr_14"].rolling(50, min_periods=5).mean()
    df["atr_expansion"] = (df["atr_14"] / atr_roll.replace(0, float("nan"))).clip(0.1, 10.0).fillna(1.0)

    # Price position relative to EMAs
    df["above_ema50"]  = (df["close"] > df["ema_50"]).astype(int)
    df["above_ema200"] = (df["close"] > df["ema_200"]).astype(int)

    # EMA alignment: how many EMAs are in bullish order (0–3)
    df["ema_alignment"] = (
        (df["ema_9"]  > df["ema_21"]).astype(int) +
        (df["ema_21"] > df["ema_50"]).astype(int) +
        (df["ema_50"] > df["ema_200"]).astype(int)
    )

    # Price velocity: (close - close[3 bars ago]) / atr_14 — normalized momentum
    df["price_velocity"] = (
        (df["close"] - df["close"].shift(3)) / df["atr_14"].replace(0, float("nan"))
    ).fillna(0.0).clip(-5.0, 5.0)

    return df


def get_current_indicators(df: pd.DataFrame) -> dict:
    """Return the latest indicator values as a flat dict."""
    if df.empty:
        return {}
    last = df.iloc[-1]
    return {
        "close":          float(last["close"]),
        "ema_9":          float(last.get("ema_9", 0)),
        "ema_21":         float(last.get("ema_21", 0)),
        "ema_50":         float(last.get("ema_50", 0)),
        "ema_200":        float(last.get("ema_200", 0)),
        "atr_14":         float(last.get("atr_14", 0)),
        "rsi_14":         float(last.get("rsi_14", 50)),
        "vol_ma_20":      float(last.get("vol_ma_20", 0)),
        "volume":         float(last.get("volume", 0)),
        "above_ema50":    int(last.get("above_ema50", 0)),
        "above_ema200":   int(last.get("above_ema200", 0)),
        "body_atr_ratio": float(last.get("body_atr_ratio", 0)),
        "atr_expansion":  float(last.get("atr_expansion", 1.0)),
        "ema_alignment":  float(last.get("ema_alignment", 1.5)),
        "price_velocity": float(last.get("price_velocity", 0.0)),
    }
