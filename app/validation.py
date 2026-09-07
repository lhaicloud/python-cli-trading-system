"""Shared backtest validation rules for pool rotation and reporting."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.config import Settings


def passes_backtest_validation(
    cfg: "Settings",
    *,
    net_profit: float,
    win_rate: float,
    total_trades: int,
    profit_factor: float = 0.0,
) -> tuple[bool, str]:
    """
    Return (passed, tier) where tier is 'strict', 'expectancy', or ''.

    Strict tier: original gates (WR >= 50%, trades >= 8, profit >= $100).
    Expectancy tier: high-R:R systems ??? WR >= 40% but strong PF + sample + profit.
    Losers (negative net, PF < 1) never pass either tier.
    """
    if (
        net_profit > cfg.backtest_min_profit
        and win_rate >= cfg.backtest_min_win_rate
        and total_trades >= cfg.backtest_min_trades
    ):
        return True, "strict"

    if not cfg.backtest_expectancy_enabled:
        return False, ""

    if (
        net_profit >= cfg.backtest_expectancy_min_profit
        and win_rate >= cfg.backtest_expectancy_min_win_rate
        and total_trades >= cfg.backtest_expectancy_min_trades
        and profit_factor >= cfg.backtest_expectancy_min_pf
    ):
        return True, "expectancy"

    return False, ""

