"""
Ratchet stop engine — progressive stop management for open trades.

Levels (defaults):
  +1R price → move stop to breakeven (lock 0R profit)
  +2R price → lock 0.75R profit
  +3R price → lock 1.50R profit
  +4R+ price → ATR trailing stop (2×ATR)

Stop only moves in the profit direction — never loosens.
"""

from __future__ import annotations


def build_ratchet_levels(entry: float, stop_loss: float, direction: str) -> list[dict]:
    """Return ratchet checkpoint definitions for a trade."""
    return [
        {"r_multiple": 1.0, "action": "lock_profit", "lock_r": 0.0},
        {"r_multiple": 2.0, "action": "lock_profit", "lock_r": 0.75},
        {"r_multiple": 3.0, "action": "lock_profit", "lock_r": 1.50},
        {"r_multiple": 4.0, "action": "atr_trail",   "lock_r": None},
    ]


def apply_ratchet(
    direction: str,
    original_risk: float,
    current_sl: float,
    candle_high: float,
    candle_low: float,
    atr: float,
    ratchet_level: int,
    ratchet_levels: list[dict],
    entry: float,
) -> tuple[float, int]:
    """
    Check if next ratchet level was reached and update stop accordingly.

    Returns (new_sl, new_ratchet_level).
    Stop only moves in the profit direction — never loosens.
    """
    if not ratchet_levels or original_risk <= 0:
        return current_sl, ratchet_level

    # All defined levels activated — maintain ATR trail every candle
    if ratchet_level >= len(ratchet_levels):
        atr_distance = max(atr * 2, 1.0)
        if direction == "BUY":
            trail_sl = candle_high - atr_distance
            return max(current_sl, trail_sl), ratchet_level
        else:
            trail_sl = candle_low + atr_distance
            return min(current_sl, trail_sl), ratchet_level

    level = ratchet_levels[ratchet_level]
    target_price = (
        entry + level["r_multiple"] * original_risk
        if direction == "BUY"
        else entry - level["r_multiple"] * original_risk
    )

    price_reached = (
        candle_high >= target_price if direction == "BUY" else candle_low <= target_price
    )

    if not price_reached:
        return current_sl, ratchet_level

    if level["action"] == "atr_trail":
        atr_distance = max(atr * 2, 1.0)
        new_sl = (
            candle_high - atr_distance if direction == "BUY" else candle_low + atr_distance
        )
    else:
        lock_r = level["lock_r"]
        new_sl = (
            entry + lock_r * original_risk
            if direction == "BUY"
            else entry - lock_r * original_risk
        )

    # Stop only moves in profit direction
    if direction == "BUY":
        new_sl = max(new_sl, current_sl)
    else:
        new_sl = min(new_sl, current_sl)

    return new_sl, ratchet_level + 1
