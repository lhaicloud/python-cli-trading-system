"""Fail-closed fixed-fractional and volatility-adjusted position sizing."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SizingLimits:
    risk_pct: float = 0.5
    max_notional_pct: float = 25.0
    target_volatility_pct: float = 2.0
    min_volatility_factor: float = 0.25
    max_volatility_factor: float = 1.0


@dataclass(frozen=True)
class PositionSize:
    quantity: float
    risk_amount: float
    notional: float
    volatility_factor: float


def fixed_fractional_size(
    *,
    equity: float,
    entry_price: float,
    stop_price: float,
    limits: SizingLimits | None = None,
    observed_volatility_pct: float | None = None,
) -> PositionSize:
    """Size from stop distance, then optionally reduce risk in high volatility.

    No leverage multiplier is applied to risk. Quantity is constrained by both
    stop-risk and maximum notional exposure.
    """
    lim = limits or SizingLimits()
    if equity <= 0 or entry_price <= 0 or stop_price <= 0:
        raise ValueError("equity and prices must be positive")
    stop_distance = abs(entry_price - stop_price)
    if stop_distance <= 0:
        raise ValueError("entry and stop must differ")
    if not 0 < lim.risk_pct <= 100:
        raise ValueError("risk_pct must be in (0, 100]")
    if not 0 < lim.max_notional_pct <= 100:
        raise ValueError("max_notional_pct must be in (0, 100]")

    factor = 1.0
    if observed_volatility_pct is not None:
        if observed_volatility_pct <= 0 or lim.target_volatility_pct <= 0:
            raise ValueError("volatility inputs must be positive")
        factor = lim.target_volatility_pct / observed_volatility_pct
        factor = max(lim.min_volatility_factor, min(lim.max_volatility_factor, factor))

    risk_budget = equity * (lim.risk_pct / 100.0) * factor
    qty_by_risk = risk_budget / stop_distance
    max_notional = equity * (lim.max_notional_pct / 100.0)
    qty_by_notional = max_notional / entry_price
    quantity = min(qty_by_risk, qty_by_notional)
    if quantity <= 0:
        raise ValueError("computed quantity is non-positive")

    risk_amount = quantity * stop_distance
    notional = quantity * entry_price
    return PositionSize(quantity, risk_amount, notional, factor)
