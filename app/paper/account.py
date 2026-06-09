"""Paper trading account state."""

from __future__ import annotations

from app.data.repository import get_setting, set_setting
from app.utils.logger import get_logger

logger = get_logger(__name__)


def get_paper_capital(symbol: str) -> float:
    val = get_setting(f"paper_capital_{symbol.lower()}")
    try:
        return float(val)
    except (ValueError, TypeError):
        return 10_000.0


def set_paper_capital(symbol: str, capital: float) -> None:
    set_setting(f"paper_capital_{symbol.lower()}", str(round(capital, 2)))


def update_capital_after_trade(symbol: str, pnl: float) -> float:
    current = get_paper_capital(symbol)
    new_capital = current + pnl
    set_paper_capital(symbol, new_capital)
    logger.info("[Paper] Capital updated: %.2f → %.2f (pnl=%.2f)", current, new_capital, pnl)
    return new_capital
