"""Explicit execution-cost accounting for research/backtests."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionCosts:
    """Per-trade monetary costs.

    Fees, spread, and slippage are transaction costs. Funding is tracked
    separately because it is a holding cost rather than an execution cost.
    """

    fees: float = 0.0
    spread: float = 0.0
    slippage: float = 0.0
    funding: float = 0.0

    @property
    def transaction_total(self) -> float:
        return self.fees + self.spread + self.slippage

    @property
    def total(self) -> float:
        return self.transaction_total + self.funding

    def stress_transaction_costs(self, multiplier: float) -> "ExecutionCosts":
        if multiplier <= 0:
            raise ValueError("cost multiplier must be positive")
        return ExecutionCosts(
            fees=self.fees * multiplier,
            spread=self.spread * multiplier,
            slippage=self.slippage * multiplier,
            funding=self.funding,
        )


def net_pnl_after_costs(gross_pnl: float, costs: ExecutionCosts) -> float:
    return gross_pnl - costs.total


def stressed_net_pnl(gross_pnl: float, costs: ExecutionCosts, multiplier: float) -> float:
    return gross_pnl - costs.stress_transaction_costs(multiplier).total
