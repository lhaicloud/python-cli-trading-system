"""Known Binance contract/methodology breaks for research segmentation.

Only precise timestamps are used for automatic window splitting. Date-only
notices remain advisory until the exact effective timestamp is verified from a
source or historical metadata snapshot; this prevents invented boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone


@dataclass(frozen=True)
class StructuralBreak:
    name: str
    symbols: frozenset[str]
    effective_date_utc: date
    effective_at_ms: int | None
    category: str
    note: str


COMMODITY_TRADFI = frozenset({
    "XAUUSDT", "XAGUSDT", "XPTUSDT", "XPDUSDT",
    "COPPERUSDT", "CLUSDT", "BZUSDT", "NATGASUSDT",
})

KNOWN_BREAKS: tuple[StructuralBreak, ...] = (
    StructuralBreak(
        name="xau_xag_funding_schedule_change",
        symbols=frozenset({"XAUUSDT", "XAGUSDT"}),
        effective_date_utc=date(2026, 1, 30),
        effective_at_ms=None,
        category="funding",
        note=(
            "Binance announced XAUUSDT/XAGUSDT funding interval/cap changes "
            "effective 2026-01-30. Exact timestamp is intentionally not guessed; "
            "use a verified announcement timestamp or stored funding snapshot."
        ),
    ),
    StructuralBreak(
        name="commodity_off_hours_orderbook_ewma_index",
        symbols=COMMODITY_TRADFI,
        effective_date_utc=date(2026, 5, 8),
        effective_at_ms=int(
            datetime(2026, 5, 8, 21, 0, tzinfo=timezone.utc).timestamp() * 1000
        ),
        category="index_methodology",
        note=(
            "Commodity TradFi perpetual off-hours/weekend/holiday price-index "
            "methodology changed to Orderbook EWMA at 2026-05-08 21:00 UTC."
        ),
    ),
)


def breaks_for(symbol: str) -> tuple[StructuralBreak, ...]:
    symbol = symbol.upper()
    return tuple(b for b in KNOWN_BREAKS if symbol in b.symbols)


def precise_segment_boundaries(symbol: str, start_ms: int, end_ms: int) -> tuple[int, ...]:
    """Return verified timestamp boundaries inside a research window."""
    if start_ms >= end_ms:
        raise ValueError("start_ms must be before end_ms")
    points = {start_ms, end_ms}
    for item in breaks_for(symbol):
        if item.effective_at_ms is not None and start_ms < item.effective_at_ms < end_ms:
            points.add(item.effective_at_ms)
    return tuple(sorted(points))


def segment_windows(symbol: str, start_ms: int, end_ms: int) -> tuple[tuple[int, int], ...]:
    points = precise_segment_boundaries(symbol, start_ms, end_ms)
    return tuple((a, b) for a, b in zip(points, points[1:]))
