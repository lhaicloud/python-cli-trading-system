"""Paper trading account state.

Two ledgers:
  - paper_capital_<symbol>   — legacy per-symbol ledger, kept for reports and
                               reconciliation (analyze-trades).
  - paper_capital_portfolio  — single portfolio equity. Position sizing and
                               all risk budgets read THIS, so risk is defined
                               against one capital base instead of N silos.

Bootstrapped on first read as default_capital + sum of all closed PnL, so the
unified equity is continuous with the existing trade history.
"""

from __future__ import annotations

from app.config import get_settings
from app.data.repository import get_setting, set_setting, get_total_closed_pnl
from app.utils.logger import get_logger

logger = get_logger(__name__)

_PORTFOLIO_KEY = "paper_capital_portfolio"


def get_paper_capital(symbol: str) -> float:
    """Legacy per-symbol ledger (reports/reconciliation only — not sizing)."""
    val = get_setting(f"paper_capital_{symbol.lower()}")
    try:
        return float(val)
    except (ValueError, TypeError):
        return get_settings().default_capital


def set_paper_capital(symbol: str, capital: float) -> None:
    set_setting(f"paper_capital_{symbol.lower()}", str(round(capital, 2)))


def get_portfolio_capital() -> float:
    """Unified portfolio equity — the capital base for sizing and risk caps."""
    val = get_setting(_PORTFOLIO_KEY)
    if val is not None:
        try:
            return float(val)
        except (ValueError, TypeError):
            pass
    cfg = get_settings()
    capital = round(cfg.default_capital + get_total_closed_pnl(), 2)
    set_setting(_PORTFOLIO_KEY, str(capital))
    logger.info("[Paper] Portfolio equity bootstrapped: %.2f", capital)
    return capital


def set_portfolio_capital(capital: float) -> None:
    set_setting(_PORTFOLIO_KEY, str(round(capital, 2)))


def update_capital_after_trade(symbol: str, pnl: float) -> float:
    """Apply realized PnL to both ledgers. Returns the new portfolio equity."""
    sym_cap = get_paper_capital(symbol) + pnl
    set_paper_capital(symbol, sym_cap)

    portfolio = get_portfolio_capital() + pnl
    set_portfolio_capital(portfolio)
    logger.info(
        "[Paper] Capital updated: %s=%.2f  portfolio=%.2f (pnl=%.2f)",
        symbol, sym_cap, portfolio, pnl,
    )
    return portfolio
