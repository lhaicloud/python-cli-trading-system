"""
Paper trading simulator — backward-compatible wrapper around PositionManager.

The actual trade logic lives in app/paper/position_manager.py.
These module-level functions keep the same signatures so existing callers
(trade_manager.py, any scripts) continue to work unchanged.
"""

from __future__ import annotations

from app.paper.position_manager import PositionManager, _update_snapshot_outcome
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Shared instance used by the standalone paper command and trade_manager.
# The live watcher uses its own PositionManager instance with explicit symbol lists.
_default_pm = PositionManager(symbols=[], risk_pct=1.0)


def try_open_paper_trade(
    symbol: str,
    signal,          # SignalResult
    capital: float,
    risk_pct: float = 1.0,
    signal_id: int | None = None,
) -> int | None:
    """
    Open a paper trade from a signal.  Returns trade_id or None.

    Delegates to PositionManager.open() after registering the symbol
    so portfolio guards work correctly.
    """
    pm = PositionManager(symbols=[symbol], risk_pct=risk_pct)
    return pm.open(symbol, signal, capital, signal_id)


def update_paper_trades(
    symbol: str,
    current_price: float,
    capital: float = 10_000.0,
) -> list[dict]:
    """
    Check all open paper trades against current_price and close any that
    hit SL or TP.  Returns list of closed trade dicts.

    Uses check_price() (high=low=close=current_price) for backward
    compatibility.  The live watcher uses check_candle() with real H/L.
    """
    pm = PositionManager(symbols=[symbol], risk_pct=1.0)
    return pm.check_price(symbol, current_price)
