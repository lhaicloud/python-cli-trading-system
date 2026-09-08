"""No-network verification for research robustness, risk, and promotion gates.

Run:
    python -m scripts.verify_research_governance
"""

from __future__ import annotations

from app.research.costs import ExecutionCosts, stressed_net_pnl
from app.research.promotion import StrategyEvidence, StrategyStatus, evaluate_promotion
from app.research.risk import CandidateRisk, OpenRisk, evaluate_candidate_risk
from app.research.robustness import (
    majority_symbols_positive,
    monte_carlo_trade_order,
    parameter_neighborhood_stable,
    profit_concentration,
)
from app.research.splits import chronological_split

failures = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global failures
    status = "PASS" if condition else "FAIL"
    if not condition:
        failures += 1
    print(f"  [{status}] {name}{(' — ' + detail) if detail else ''}")


print("\n1. Cost stress")
costs = ExecutionCosts(fees=1.0, spread=2.0, slippage=3.0, funding=4.0)
stressed = costs.stress_transaction_costs(1.5)
check("fees stressed", stressed.fees == 1.5)
check("spread stressed", stressed.spread == 3.0)
check("slippage stressed", stressed.slippage == 4.5)
check("funding kept separate", stressed.funding == 4.0)
check("stressed net PnL correct", stressed_net_pnl(20.0, costs, 1.5) == 7.0)

print("\n2. Chronological split")
split = chronological_split(list(range(100)))
check("development starts first", split.development[0] == 0)
check("validation follows development", split.validation[0] == 60)
check("untouched test is last", split.untouched_test[0] == 80 and split.untouched_test[-1] == 99)

print("\n3. Robustness helpers")
mc = monte_carlo_trade_order([10, -5, 8, -3, -2, 6], initial_capital=1000, iterations=100, seed=1)
check("Monte Carlo runs requested iterations", mc.iterations == 100)
check("Monte Carlo drawdown non-negative", mc.p95_max_drawdown_pct >= 0)
check("majority symbol expectancy", majority_symbols_positive({"A": 1, "B": 2, "C": -1}))
check("profit concentration calculated", abs(profit_concentration({"A": 40, "B": 30, "C": 30}) - 0.4) < 1e-9)
check("parameter neighborhood stability", parameter_neighborhood_stable([1, 2, 3, -1, -2], 0.6))


def evidence(**overrides):
    base = dict(
        expectancy_after_costs=1.0,
        profit_factor=1.5,
        historical_trades=250,
        untouched_trades=120,
        untouched_expectancy=0.8,
        expectancy_at_1_5x_costs=0.3,
        symbol_expectancy={"A": 1.0, "B": 0.5, "C": 0.2, "D": -0.1, "E": -0.2},
        symbol_profit={"A": 30.0, "B": 25.0, "C": 20.0, "D": 15.0, "E": 10.0},
        max_drawdown_pct=10.0,
        drawdown_budget_pct=15.0,
        parameter_neighborhood_stable=True,
        entry_delay_robust=True,
        slippage_robust=True,
        market_data_integrity_passed=True,
        execution_cost_evidence_verified=True,
        structural_breaks_handled=True,
        asset_class_model_verified=True,
        shadow_days=60,
        shadow_expectancy=0.2,
    )
    base.update(overrides)
    return StrategyEvidence(**base)


print("\n4. Promotion gates")
low_sample = evaluate_promotion(evidence(historical_trades=50, untouched_trades=20))
check("low sample remains research-only", low_sample.status is StrategyStatus.RESEARCH_ONLY)
check("low sample not promoted", not low_sample.passed)

missing_provenance = evaluate_promotion(evidence(execution_cost_evidence_verified=False))
check("missing execution evidence remains research-only", missing_provenance.status is StrategyStatus.RESEARCH_ONLY)
check("execution evidence gate fails closed", any(g.name == "execution_cost_evidence" and not g.passed for g in missing_provenance.gates))

concentrated = evaluate_promotion(evidence(symbol_profit={"A": 90, "B": 5, "C": 5}))
check("concentrated strategy rejected", concentrated.status is StrategyStatus.REJECTED)
check("concentration gate fails", any(g.name == "profit_concentration" and not g.passed for g in concentrated.gates))

shadow = evaluate_promotion(evidence(shadow_days=20, shadow_expectancy=0.2))
check("core-pass strategy waits in SHADOW", shadow.status is StrategyStatus.SHADOW)
check("shadow duration prevents promotion", not shadow.passed)

eligible = evaluate_promotion(evidence())
check("all mandatory gates reach LIVE_ELIGIBLE", eligible.status is StrategyStatus.LIVE_ELIGIBLE)
check("LIVE_ELIGIBLE decision marked passed", eligible.passed)

print("\n5. Portfolio risk")
open_risk = [OpenRisk("BTCUSDT", "crypto", "BUY", 100.0, "crypto_beta")]
candidate = CandidateRisk("ETHUSDT", "crypto", "BUY", 50.0, 2.0, "crypto_beta")
missing_corr = evaluate_candidate_risk(
    equity=10_000,
    candidate=candidate,
    open_risks=open_risk,
    correlations=None,
)
check("missing correlation fails closed", not missing_corr.allowed and "missing_correlation_data" in missing_corr.reasons)

allowed = evaluate_candidate_risk(
    equity=10_000,
    candidate=candidate,
    open_risks=open_risk,
    correlations={("ETHUSDT", "BTCUSDT"): 0.5},
)
check("within-cap candidate accepted", allowed.allowed, str(allowed.reasons))

missing_funding = evaluate_candidate_risk(
    equity=10_000,
    candidate=CandidateRisk("ETHUSDT", "crypto", "BUY", 50.0, None, "crypto_beta"),
    open_risks=[],
    correlations={},
)
check("missing funding assumption fails closed", not missing_funding.allowed and "missing_expected_funding_cost" in missing_funding.reasons)

print(f"\n{'ALL CHECKS PASSED' if failures == 0 else f'{failures} CHECK(S) FAILED'}")
raise SystemExit(1 if failures else 0)
