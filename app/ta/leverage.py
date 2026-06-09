"""
Dynamic leverage calculator — risk-aware position scaling for futures.

Computes a leverage multiplier (1 to max_leverage) based on:
  1. Signal conviction   — confidence, zone score, R:R, MTF gap
  2. Market regime       — trending markets earn higher multipliers
  3. ML model quality    — rule-based signals are scaled down
  4. Recent performance  — losing streaks force the multiplier to 1×

Usage:
    from app.ta.leverage import dynamic_leverage
    lev = dynamic_leverage(signal, recent_trades=trades, max_leverage=5)
    pos_size = base_position_size * lev
"""
from __future__ import annotations

from app.utils.logger import get_logger

logger = get_logger(__name__)

_STRONG_REGIMES = {"bullish_trend", "bearish_trend"}
_WEAK_REGIMES   = {"choppy", "squeeze", "high_volatility_liquidation"}


def dynamic_leverage(
    signal,                        # SignalResult (typed loosely to avoid circular import)
    recent_trades: list[dict] | None = None,
    max_leverage: int = 5,
) -> int:
    """
    Return an integer leverage multiplier in [1, max_leverage].

    Parameters
    ----------
    signal        : SignalResult from generate_signal()
    recent_trades : List of closed trade dicts with 'pnl' key, most-recent last.
                    Used only for the drawdown damper — pass [] to skip.
    max_leverage  : Hard ceiling (default 5).

    Decision logic
    --------------
    Start at 1.0, then apply multiplicative boosts and dampers:

    Boosts (increase leverage when conviction is high):
      confidence ≥ 80 → ×2.0   |  ≥ 72 → ×1.5
      zone_score ≥ 80 → ×1.3   |  ≥ 70 → ×1.1
      risk_reward ≥ 3 → ×1.2   |  ≥ 2.5 → ×1.1
      MTF gap     ≥ 50 → ×1.2  |  ≥ 40 → ×1.1
      trending regime → ×1.2

    Dampers (reduce leverage when conditions are uncertain):
      choppy/squeeze/high-vol regime → floor to 1.0
      rule-based model (no ML)       → ×0.7
      recent WR < 40% (last 5)       → ×0.5
      ≥ 3 consecutive losses         → force to 1 (hard reset)
    """
    if max_leverage <= 1:
        return 1

    lev = 1.0

    # ── 1. Signal confidence ──────────────────────────────────────────────────
    conf = getattr(signal, "confidence", 0)
    if conf >= 80:
        lev *= 2.0
    elif conf >= 72:
        lev *= 1.5
    # below 65: min_signal_confidence gate should have blocked, but no boost

    # ── 2. Zone score quality ─────────────────────────────────────────────────
    zs = getattr(signal, "zone_score", 0)
    if zs >= 80:
        lev *= 1.3
    elif zs >= 70:
        lev *= 1.1

    # ── 3. Risk-reward ratio ──────────────────────────────────────────────────
    rr = getattr(signal, "risk_reward", 0)
    if rr >= 3.0:
        lev *= 1.2
    elif rr >= 2.5:
        lev *= 1.1

    # ── 4. MTF conviction gap ─────────────────────────────────────────────────
    ls  = getattr(signal, "long_score",  0)
    ss  = getattr(signal, "short_score", 0)
    gap = abs(ls - ss)
    if gap >= 50:
        lev *= 1.2
    elif gap >= 40:
        lev *= 1.1

    # ── 5. Market regime ──────────────────────────────────────────────────────
    regime = getattr(signal, "market_regime", "")
    if regime in _STRONG_REGIMES:
        lev *= 1.2
    elif regime in _WEAK_REGIMES:
        lev = min(lev, 1.0)   # cap at 1× — no boost in weak regimes

    # ── 6. ML model quality ───────────────────────────────────────────────────
    mv = getattr(signal, "model_version", "rule_based_v1")
    if mv == "rule_based_v1":
        lev *= 0.7   # rule-based confidence is less calibrated

    # ── 7. Recent performance damper ─────────────────────────────────────────
    if recent_trades:
        last5 = list(recent_trades)[-5:]
        n     = len(last5)
        if n > 0:
            wins  = sum(1 for t in last5 if float(t.get("pnl", 0)) > 0)
            wr    = wins / n

            # Count consecutive losses from most-recent backwards
            consec_losses = 0
            for t in reversed(last5):
                if float(t.get("pnl", 0)) <= 0:
                    consec_losses += 1
                else:
                    break

            if consec_losses >= 3:
                logger.debug("[Leverage] 3+ consecutive losses — forcing 1×")
                return 1
            if wr < 0.40:
                logger.debug("[Leverage] Recent WR %.0f%% < 40%% — halving", wr * 100)
                lev *= 0.5

    # ── Final: clamp to [1, max_leverage] ────────────────────────────────────
    result = min(max_leverage, max(1, round(lev)))
    logger.debug(
        "[Leverage] conf=%.0f zs=%.0f rr=%.1f gap=%.0f regime=%s → %d×",
        conf, zs, rr, gap, regime, result,
    )
    return result
