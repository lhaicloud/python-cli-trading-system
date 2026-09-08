"""Fail-closed portfolio risk checks for research/paper promotion work."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence


@dataclass(frozen=True)
class OpenRisk:
    symbol: str
    asset_class: str
    direction: str
    risk_amount: float
    common_factor: str | None = None


@dataclass(frozen=True)
class CandidateRisk:
    symbol: str
    asset_class: str
    direction: str
    risk_amount: float
    expected_funding_cost: float | None
    common_factor: str | None = None


@dataclass(frozen=True)
class PortfolioRiskLimits:
    max_open_risk_pct: float = 3.0
    max_asset_class_risk_pct: float = 2.0
    max_common_factor_risk_pct: float = 2.0
    max_expected_funding_cost_pct: float = 0.10
    max_pair_correlation: float = 0.80
    require_correlation_data: bool = True


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reasons: tuple[str, ...]


def evaluate_candidate_risk(
    *,
    equity: float,
    candidate: CandidateRisk,
    open_risks: Sequence[OpenRisk],
    correlations: Mapping[tuple[str, str], float] | None,
    limits: PortfolioRiskLimits | None = None,
) -> RiskDecision:
    """Reject whenever a required risk input is missing or a cap is exceeded."""
    lim = limits or PortfolioRiskLimits()
    reasons: list[str] = []

    if equity <= 0:
        return RiskDecision(False, ("invalid_equity",))
    if candidate.risk_amount <= 0:
        return RiskDecision(False, ("invalid_candidate_risk",))
    if any(p.symbol == candidate.symbol for p in open_risks):
        reasons.append("duplicate_symbol_entry")

    total_risk = sum(max(0.0, p.risk_amount) for p in open_risks) + candidate.risk_amount
    if total_risk / equity * 100 > lim.max_open_risk_pct:
        reasons.append("portfolio_open_risk_cap")

    class_risk = (
        sum(max(0.0, p.risk_amount) for p in open_risks if p.asset_class == candidate.asset_class)
        + candidate.risk_amount
    )
    if class_risk / equity * 100 > lim.max_asset_class_risk_pct:
        reasons.append("asset_class_risk_cap")

    if candidate.common_factor:
        factor_risk = (
            sum(max(0.0, p.risk_amount) for p in open_risks if p.common_factor == candidate.common_factor)
            + candidate.risk_amount
        )
        if factor_risk / equity * 100 > lim.max_common_factor_risk_pct:
            reasons.append("common_factor_risk_cap")

    if candidate.expected_funding_cost is None:
        reasons.append("missing_expected_funding_cost")
    elif candidate.expected_funding_cost < 0:
        reasons.append("invalid_expected_funding_cost")
    elif candidate.expected_funding_cost / equity * 100 > lim.max_expected_funding_cost_pct:
        reasons.append("funding_cost_cap")

    if open_risks:
        if correlations is None and lim.require_correlation_data:
            reasons.append("missing_correlation_data")
        elif correlations is not None:
            for position in open_risks:
                key = (candidate.symbol, position.symbol)
                reverse = (position.symbol, candidate.symbol)
                corr = correlations.get(key, correlations.get(reverse))
                if corr is None:
                    if lim.require_correlation_data:
                        reasons.append(f"missing_correlation:{position.symbol}")
                    continue
                if abs(float(corr)) > lim.max_pair_correlation:
                    reasons.append(f"correlation_cap:{position.symbol}")

    return RiskDecision(not reasons, tuple(reasons))
