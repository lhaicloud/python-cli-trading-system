"""Research robustness metrics independent of any one strategy implementation."""

from __future__ import annotations

import random
from dataclasses import dataclass
from statistics import median
from typing import Mapping, Sequence


@dataclass(frozen=True)
class MonteCarloSummary:
    iterations: int
    median_max_drawdown_pct: float
    p95_max_drawdown_pct: float
    median_max_losing_streak: int
    p95_max_losing_streak: int


def max_drawdown_pct(pnls: Sequence[float], initial_capital: float) -> float:
    if initial_capital <= 0:
        raise ValueError("initial_capital must be positive")
    equity = initial_capital
    peak = initial_capital
    worst = 0.0
    for pnl in pnls:
        equity += float(pnl)
        peak = max(peak, equity)
        if peak > 0:
            worst = max(worst, (peak - equity) / peak * 100.0)
    return worst


def max_losing_streak(pnls: Sequence[float]) -> int:
    best = current = 0
    for pnl in pnls:
        if pnl <= 0:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def _percentile(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(v) for v in values)
    idx = int(round((len(ordered) - 1) * q))
    return ordered[max(0, min(idx, len(ordered) - 1))]


def monte_carlo_trade_order(
    pnls: Sequence[float],
    initial_capital: float,
    iterations: int = 1000,
    seed: int = 7,
) -> MonteCarloSummary:
    """Shuffle trade order while preserving the empirical PnL distribution."""
    if iterations <= 0:
        raise ValueError("iterations must be positive")
    if not pnls:
        raise ValueError("pnls must not be empty")

    rng = random.Random(seed)
    source = [float(p) for p in pnls]
    drawdowns: list[float] = []
    streaks: list[int] = []

    for _ in range(iterations):
        sample = source.copy()
        rng.shuffle(sample)
        drawdowns.append(max_drawdown_pct(sample, initial_capital))
        streaks.append(max_losing_streak(sample))

    return MonteCarloSummary(
        iterations=iterations,
        median_max_drawdown_pct=round(float(median(drawdowns)), 4),
        p95_max_drawdown_pct=round(_percentile(drawdowns, 0.95), 4),
        median_max_losing_streak=int(round(float(median(streaks)))),
        p95_max_losing_streak=int(round(_percentile(streaks, 0.95))),
    )


def profit_concentration(symbol_profit: Mapping[str, float]) -> float:
    """Largest positive symbol contribution divided by total positive profit."""
    positives = {s: max(0.0, float(p)) for s, p in symbol_profit.items()}
    total = sum(positives.values())
    if total <= 0:
        return 1.0
    return max(positives.values(), default=0.0) / total


def majority_symbols_positive(symbol_expectancy: Mapping[str, float]) -> bool:
    if not symbol_expectancy:
        return False
    positive = sum(1 for value in symbol_expectancy.values() if float(value) > 0)
    return positive > len(symbol_expectancy) / 2


def median_symbol_expectancy(symbol_expectancy: Mapping[str, float]) -> float:
    if not symbol_expectancy:
        return 0.0
    return float(median(float(v) for v in symbol_expectancy.values()))


def parameter_neighborhood_stable(
    neighbor_expectancies: Sequence[float],
    minimum_positive_fraction: float = 0.60,
) -> bool:
    if not neighbor_expectancies:
        return False
    if not 0 < minimum_positive_fraction <= 1:
        raise ValueError("minimum_positive_fraction must be in (0, 1]")
    positive = sum(1 for value in neighbor_expectancies if float(value) > 0)
    return positive / len(neighbor_expectancies) >= minimum_positive_fraction
