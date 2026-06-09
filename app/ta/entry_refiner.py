"""
5m entry refinement — improves entry price after a 30m signal fires.

For BUY:  look for a recent 5m pullback absorbed by a bullish close near the
          entry zone; use the low of the confirmation candle as refined entry.
For SELL: look for a recent 5m bounce rejected by a bearish close near the
          entry zone; use the high of the rejection candle as refined entry.

Always falls back to the original 30m entry_price — no trade is ever blocked.
"""

from __future__ import annotations

import pandas as pd


def refine_entry(
    signal:          str,
    entry_price:     float,
    stop_loss:       float,
    df_5m:           pd.DataFrame,
    tolerance_pct:   float = 0.20,   # within 0.20% of entry = "near zone"
    chase_limit_pct: float = 0.40,   # > 0.40% past entry   = "price ran"
) -> tuple[float, str]:
    """
    Return (refined_entry_price, reason_string).

    Parameters
    ----------
    signal          : "BUY" or "SELL"
    entry_price     : 30m signal entry price
    stop_loss       : 30m stop loss (used as context; not the primary gate)
    df_5m           : recent 5m candle DataFrame (need at least 3 rows)
    tolerance_pct   : % band around entry_price considered "at the zone"
    chase_limit_pct : if price moved this far past entry, flag as stale
    """
    if df_5m.empty or len(df_5m) < 3:
        return entry_price, "5m_no_data"

    last          = df_5m.iloc[-1]
    current_price = float(last["close"])
    tol           = entry_price * (tolerance_pct   / 100)
    chase         = entry_price * (chase_limit_pct / 100)

    recent        = df_5m.iloc[-6:]   # last 30 minutes of 5m candles
    last_bullish  = float(last["close"]) > float(last["open"])
    last_bearish  = float(last["close"]) < float(last["open"])
    near_entry    = abs(current_price - entry_price) <= tol

    prior = df_5m.iloc[-6:-1]

    if signal == "BUY":
        stale     = current_price > entry_price + chase
        last_low  = float(last["low"])
        prior_swept = float(prior["low"].min()) < entry_price if not prior.empty else False

        # Best: LAST candle itself dipped below entry and recovered — enter at the dip
        if not stale and last_bullish and last_low < entry_price and near_entry:
            return round(last_low, 6), "5m_pullback_absorbed"

        # Good: a prior candle swept below, last candle confirms fully above entry
        if not stale and last_bullish and prior_swept and near_entry:
            return entry_price, "5m_bullish_confirmed"

        # Clean: bullish momentum right at zone, no sweeps
        if not stale and last_bullish and near_entry:
            return entry_price, "5m_bullish_confirmed"

        if stale:
            return entry_price, "5m_entry_stale"

        return entry_price, "5m_fallback"

    elif signal == "SELL":
        stale      = current_price < entry_price - chase
        last_high  = float(last["high"])
        prior_swept = float(prior["high"].max()) > entry_price if not prior.empty else False

        # Best: LAST candle itself spiked above entry and rejected — enter at the spike
        if not stale and last_bearish and last_high > entry_price and near_entry:
            return round(last_high, 6), "5m_bounce_rejected"

        # Good: a prior candle spiked above, last candle confirms fully below entry
        if not stale and last_bearish and prior_swept and near_entry:
            return entry_price, "5m_bearish_confirmed"

        # Clean: bearish momentum right at zone, no spikes
        if not stale and last_bearish and near_entry:
            return entry_price, "5m_bearish_confirmed"

        if stale:
            return entry_price, "5m_entry_stale"

        return entry_price, "5m_fallback"

    return entry_price, "5m_no_signal"
