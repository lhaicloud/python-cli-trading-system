"""Evidence aggregation for chronological and robustness validation.

This module does not optimize parameters and does not authorize live trading.
It consumes already-generated research trade records from isolated development,
validation, untouched, and stress runs and derives promotion evidence from them.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean
from typing import Mapping, Sequence

from app.research.backtester import TradeRecord
from app.research.promotion import (
    PromotionDecision,
    StrategyEvidence,
    evaluate_promotion,
)
from app.research.robustness import (
    MonteCarloSummary,
    max_drawdown_pct,
    median_symbol_expectancy,
    monte_carlo_trade_order,
    parameter_neighborhood_stable,
    profit_concentration,
)


@dataclass(frozen=True)
class TradeMetrics:
    trades: int
    expectancy: float
    profit_factor: float
    win_rate: float
    average_win: float
    average_loss: float
    max_drawdown_pct: float
    fees: float
    spread_cost: float
    slippage_cost: float
    funding_cost: float


@dataclass(frozen=True)
class ValidationBundle:
    development: Sequence[TradeRecord]
    validation: Sequence[TradeRecord]
    untouched_test: Sequence[TradeRecord]
    entry_delay_variants: Mapping[str, Sequence[TradeRecord]]
    slippage_variants: Mapping[str, Sequence[TradeRecord]]
    parameter_neighbor_expectancies: Sequence[float]
    initial_capital: float
    drawdown_budget_pct: float
    shadow_days: int = 0
    shadow_expectancy: float | None = None


@dataclass(frozen=True)
class ValidationReport:
    development: TradeMetrics
    validation: TradeMetrics
    untouched_test: TradeMetrics
    combined: TradeMetrics
    cost_stress_1_5x_expectancy: float
    cost_stress_2x_expectancy: float
    entry_delay_expectancies: Mapping[str, float]
    slippage_variant_expectancies: Mapping[str, float]
    symbol_expectancy: Mapping[str, float]
    asset_class_expectancy: Mapping[str, float]
    regime_expectancy: Mapping[str, float]
    median_symbol_expectancy: float
    profit_concentration: float
    monte_carlo: MonteCarloSummary | None
    promotion: PromotionDecision


def _pnl(record: TradeRecord, transaction_cost_multiplier: float = 1.0) -> float:
    if transaction_cost_multiplier <= 0:
        raise ValueError("transaction_cost_multiplier must be positive")
    transaction = record.fees + record.spread_cost + record.slippage_cost
    return record.gross_mid_pnl - transaction * transaction_cost_multiplier - record.funding_cost


def metrics(
    records: Sequence[TradeRecord],
    initial_capital: float,
    transaction_cost_multiplier: float = 1.0,
) -> TradeMetrics:
    pnls = [_pnl(r, transaction_cost_multiplier) for r in records]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    pf = gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)
    return TradeMetrics(
        trades=len(records),
        expectancy=mean(pnls) if pnls else 0.0,
        profit_factor=pf,
        win_rate=len(wins) / len(pnls) if pnls else 0.0,
        average_win=mean(wins) if wins else 0.0,
        average_loss=abs(mean(losses)) if losses else 0.0,
        max_drawdown_pct=max_drawdown_pct(pnls, initial_capital) if pnls else 0.0,
        fees=sum(r.fees for r in records),
        spread_cost=sum(r.spread_cost for r in records),
        slippage_cost=sum(r.slippage_cost for r in records),
        funding_cost=sum(r.funding_cost for r in records),
    )


def _group_expectancy(records: Sequence[TradeRecord], field: str) -> dict[str, float]:
    groups: dict[str, list[float]] = {}
    for record in records:
        if field == "regime":
            # Regime is optional research metadata; absence is explicit rather
            # than silently treated as a known regime.
            key = str(getattr(record, "regime", "UNSPECIFIED"))
        else:
            key = str(getattr(record, field))
        groups.setdefault(key, []).append(record.net_pnl)
    return {key: mean(values) for key, values in groups.items()}


def _symbol_profit(records: Sequence[TradeRecord]) -> dict[str, float]:
    result: dict[str, float] = {}
    for record in records:
        result[record.symbol] = result.get(record.symbol, 0.0) + record.net_pnl
    return result


def _variant_expectancy(variants: Mapping[str, Sequence[TradeRecord]]) -> dict[str, float]:
    return {
        name: (mean(r.net_pnl for r in records) if records else 0.0)
        for name, records in variants.items()
    }


def build_validation_report(bundle: ValidationBundle) -> ValidationReport:
    if bundle.initial_capital <= 0:
        raise ValueError("initial_capital must be positive")
    combined_records = list(bundle.development) + list(bundle.validation) + list(bundle.untouched_test)

    development_metrics = metrics(bundle.development, bundle.initial_capital)
    validation_metrics = metrics(bundle.validation, bundle.initial_capital)
    untouched_metrics = metrics(bundle.untouched_test, bundle.initial_capital)
    combined_metrics = metrics(combined_records, bundle.initial_capital)
    cost_15 = metrics(combined_records, bundle.initial_capital, 1.5).expectancy
    cost_20 = metrics(combined_records, bundle.initial_capital, 2.0).expectancy

    entry_delay = _variant_expectancy(bundle.entry_delay_variants)
    slippage = _variant_expectancy(bundle.slippage_variants)
    symbol_expectancy = _group_expectancy(combined_records, "symbol")
    asset_expectancy = _group_expectancy(combined_records, "asset_class")
    regime_expectancy = _group_expectancy(combined_records, "regime")
    symbol_profit = _symbol_profit(combined_records)

    entry_delay_robust = bool(entry_delay) and all(v > 0 for v in entry_delay.values())
    slippage_robust = bool(slippage) and all(v > 0 for v in slippage.values())
    parameter_stable = parameter_neighborhood_stable(bundle.parameter_neighbor_expectancies)

    monte_carlo = None
    if combined_records:
        monte_carlo = monte_carlo_trade_order(
            [r.net_pnl for r in combined_records],
            bundle.initial_capital,
        )

    evidence = StrategyEvidence(
        expectancy_after_costs=combined_metrics.expectancy,
        profit_factor=combined_metrics.profit_factor,
        historical_trades=combined_metrics.trades,
        untouched_trades=untouched_metrics.trades,
        untouched_expectancy=untouched_metrics.expectancy,
        expectancy_at_1_5x_costs=cost_15,
        symbol_expectancy=symbol_expectancy,
        symbol_profit=symbol_profit,
        max_drawdown_pct=combined_metrics.max_drawdown_pct,
        drawdown_budget_pct=bundle.drawdown_budget_pct,
        parameter_neighborhood_stable=parameter_stable,
        entry_delay_robust=entry_delay_robust,
        slippage_robust=slippage_robust,
        shadow_days=bundle.shadow_days,
        shadow_expectancy=bundle.shadow_expectancy,
    )
    decision = evaluate_promotion(evidence)

    return ValidationReport(
        development=development_metrics,
        validation=validation_metrics,
        untouched_test=untouched_metrics,
        combined=combined_metrics,
        cost_stress_1_5x_expectancy=cost_15,
        cost_stress_2x_expectancy=cost_20,
        entry_delay_expectancies=entry_delay,
        slippage_variant_expectancies=slippage,
        symbol_expectancy=symbol_expectancy,
        asset_class_expectancy=asset_expectancy,
        regime_expectancy=regime_expectancy,
        median_symbol_expectancy=median_symbol_expectancy(symbol_expectancy),
        profit_concentration=profit_concentration(symbol_profit),
        monte_carlo=monte_carlo,
        promotion=decision,
    )
