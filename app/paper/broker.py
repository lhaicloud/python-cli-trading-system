"""Paper broker — simulated order execution (no real orders)."""

from __future__ import annotations

from app.utils.logger import get_logger

logger = get_logger(__name__)


def simulate_fill(direction: str, entry: float, slippage_pct: float = 0.05) -> float:
    """Apply simulated slippage to an entry price."""
    factor = 1 + slippage_pct / 100 if direction == "BUY" else 1 - slippage_pct / 100
    filled = round(entry * factor, 6)
    logger.debug("[Broker] Simulated fill: %s @ %.4f (slippage=%.2f%%)", direction, filled, slippage_pct)
    return filled
