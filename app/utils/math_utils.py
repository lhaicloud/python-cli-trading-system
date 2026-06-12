"""Numeric helpers used across the system."""

from __future__ import annotations

import numpy as np
import pandas as pd


def pct_change(a: float, b: float) -> float:
    """Percentage change from a to b."""
    if a == 0:
        return 0.0
    return (b - a) / abs(a) * 100


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def round_price(price: float, tick: float = 0.01) -> float:
    if tick <= 0:
        return price
    return round(round(price / tick) * tick, 10)


def atr(highs: pd.Series, lows: pd.Series, closes: pd.Series, period: int = 14) -> pd.Series:
    """Average True Range."""
    prev_close = closes.shift(1)
    tr = pd.concat(
        [
            highs - lows,
            (highs - prev_close).abs(),
            (lows - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period).mean()


def rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    delta = closes.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def body_size(open_: pd.Series, close_: pd.Series) -> pd.Series:
    return (close_ - open_).abs()


def is_bullish_candle(open_: float, close_: float) -> bool:
    return close_ > open_


def is_bearish_candle(open_: float, close_: float) -> bool:
    return close_ < open_


def risk_reward(entry: float, sl: float, tp: float) -> float:
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    if risk == 0:
        return 0.0
    return reward / risk


def position_size(
    capital: float,
    risk_pct: float,
    entry: float,
    sl: float,
    leverage: int = 1,
) -> float:
    """
    Futures-aware position sizing. Returns number of units (contracts).

    leverage > 1 scales the position up proportionally so notional exposure
    is leverage× larger while margin posted = notional / leverage.
    Effective capital at risk per trade = risk_pct × leverage %.

    Notional is capped at capital × leverage: a tight stop cannot buy more
    exposure than the account could actually post margin for (1× = spot-like).
    """
    risk_amount = capital * (risk_pct / 100)
    price_risk  = abs(entry - sl)
    if price_risk == 0 or entry <= 0:
        return 0.0
    units = (risk_amount / price_risk) * max(1, int(leverage))
    max_units = capital * max(1, int(leverage)) / entry
    return min(units, max_units)
