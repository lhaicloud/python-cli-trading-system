"""Mandatory evidence-based strategy promotion gates."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from app.research.robustness import majority_symbols_positive, profit_concentration


class StrategyStatus(str, Enum):
    REJECTED = "REJECTED"
    RESEARCH_ONLY = "RESEARCH_ONLY"
    VALIDATION = "VALIDATION"
    SHADOW = "SHADOW"
    PAPER_APPROVED = "PAPER_APPROVED"
    LIVE_ELIGIBLE = "LIVE_ELIGIBLE"


@dataclass(frozen=True)
class PromotionThresholds:
    min_profit_factor: float = 1.30
    min_historical_trades: int = 200
    min_untouched_trades: int = 100
    max_symbol_profit_concentration: float = 0.40
    min_shadow_days: int = 56


@dataclass(frozen=True)
class StrategyEvidence:
    expectancy_after_costs: float
    profit_factor: float
    historical_trades: int
    untouched_trades: int
    untouched_expectancy: float
    expectancy_at_1_5x_costs: float
    symbol_expectancy: Mapping[str, float]
    symbol_profit: Mapping[str, float]
    max_drawdown_pct: float
    drawdown_budget_pct: float
    parameter_neighborhood_stable: bool
    entry_delay_robust: bool
    slippage_robust: bool
    # Fail-closed research evidence gates. Defaults are deliberately False so
    # a caller cannot promote a strategy merely by omitting data provenance.
    market_data_integrity_passed: bool = False
    execution_cost_evidence_verified: bool = False
    structural_breaks_handled: bool = False
    asset_class_model_verified: bool = False
    shadow_days: int = 0
    shadow_expectancy: float | None = None


@dataclass(frozen=True)
class GateResult:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class PromotionDecision:
    status: StrategyStatus
    passed: bool
    gates: tuple[GateResult, ...]

    @property
    def failed_gates(self) -> tuple[GateResult, ...]:
        return tuple(g for g in self.gates if not g.passed)


def evaluate_promotion(
    evidence: StrategyEvidence,
    thresholds: PromotionThresholds | None = None,
) -> PromotionDecision:
    """Evaluate every mandatory gate; no win-rate shortcut exists here."""
    t = thresholds or PromotionThresholds()
    concentration = profit_concentration(evidence.symbol_profit)
    majority_positive = majority_symbols_positive(evidence.symbol_expectancy)

    evidence_gates = (
        GateResult(
            "market_data_integrity",
            evidence.market_data_integrity_passed,
            f"verified={evidence.market_data_integrity_passed}",
        ),
        GateResult(
            "execution_cost_evidence",
            evidence.execution_cost_evidence_verified,
            f"verified={evidence.execution_cost_evidence_verified}",
        ),
        GateResult(
            "structural_break_handling",
            evidence.structural_breaks_handled,
            f"handled={evidence.structural_breaks_handled}",
        ),
        GateResult(
            "asset_class_model",
            evidence.asset_class_model_verified,
            f"verified={evidence.asset_class_model_verified}",
        ),
    )

    performance_gates = (
        GateResult(
            "positive_expectancy_after_costs",
            evidence.expectancy_after_costs > 0,
            f"expectancy={evidence.expectancy_after_costs:.6f}",
        ),
        GateResult(
            "profit_factor",
            evidence.profit_factor >= t.min_profit_factor,
            f"profit_factor={evidence.profit_factor:.4f} required>={t.min_profit_factor:.2f}",
        ),
        GateResult(
            "historical_trade_count",
            evidence.historical_trades >= t.min_historical_trades,
            f"trades={evidence.historical_trades} required>={t.min_historical_trades}",
        ),
        GateResult(
            "untouched_trade_count",
            evidence.untouched_trades >= t.min_untouched_trades,
            f"untouched={evidence.untouched_trades} required>={t.min_untouched_trades}",
        ),
        GateResult(
            "positive_untouched_expectancy",
            evidence.untouched_expectancy > 0,
            f"untouched_expectancy={evidence.untouched_expectancy:.6f}",
        ),
        GateResult(
            "positive_expectancy_at_1_5x_costs",
            evidence.expectancy_at_1_5x_costs > 0,
            f"expectancy_1_5x={evidence.expectancy_at_1_5x_costs:.6f}",
        ),
        GateResult(
            "majority_symbols_positive",
            majority_positive,
            f"symbols={len(evidence.symbol_expectancy)} majority_positive={majority_positive}",
        ),
        GateResult(
            "profit_concentration",
            concentration <= t.max_symbol_profit_concentration,
            f"max_positive_symbol_share={concentration:.4f} required<={t.max_symbol_profit_concentration:.2f}",
        ),
        GateResult(
            "drawdown_budget",
            0 <= evidence.max_drawdown_pct <= evidence.drawdown_budget_pct,
            f"max_dd={evidence.max_drawdown_pct:.2f}% budget={evidence.drawdown_budget_pct:.2f}%",
        ),
        GateResult(
            "parameter_neighborhood_stability",
            evidence.parameter_neighborhood_stable,
            f"stable={evidence.parameter_neighborhood_stable}",
        ),
        GateResult(
            "entry_delay_robustness",
            evidence.entry_delay_robust,
            f"robust={evidence.entry_delay_robust}",
        ),
        GateResult(
            "slippage_robustness",
            evidence.slippage_robust,
            f"robust={evidence.slippage_robust}",
        ),
    )
    gates = evidence_gates + performance_gates

    core_pass = all(g.passed for g in gates)
    if not core_pass:
        # Missing samples/provenance are insufficient evidence, not proof of a
        # losing strategy. Failed economic/robustness gates with sufficient
        # evidence are classified REJECTED.
        insufficient = (
            evidence.historical_trades < t.min_historical_trades
            or evidence.untouched_trades < t.min_untouched_trades
            or not evidence.symbol_expectancy
            or not all(g.passed for g in evidence_gates)
        )
        return PromotionDecision(
            status=StrategyStatus.RESEARCH_ONLY if insufficient else StrategyStatus.REJECTED,
            passed=False,
            gates=gates,
        )

    shadow_gate = GateResult(
        "shadow_duration",
        evidence.shadow_days >= t.min_shadow_days,
        f"shadow_days={evidence.shadow_days} required>={t.min_shadow_days}",
    )
    shadow_expectancy_gate = GateResult(
        "shadow_expectancy",
        evidence.shadow_expectancy is not None and evidence.shadow_expectancy > 0,
        f"shadow_expectancy={evidence.shadow_expectancy}",
    )
    all_gates = gates + (shadow_gate, shadow_expectancy_gate)

    if not shadow_gate.passed or not shadow_expectancy_gate.passed:
        return PromotionDecision(
            status=StrategyStatus.SHADOW,
            passed=False,
            gates=all_gates,
        )

    return PromotionDecision(
        status=StrategyStatus.LIVE_ELIGIBLE,
        passed=True,
        gates=all_gates,
    )
