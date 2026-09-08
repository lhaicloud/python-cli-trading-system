"""Specialized parsers for Binance archive series with non-price semantics."""

from __future__ import annotations

from app.research.backtester import MarketBar
from app.research.binance_archive import ArchiveError, VerifiedArchive


def parse_signed_kline_bars(archive: VerifiedArchive) -> tuple[MarketBar, ...]:
    """Parse kline-shaped series whose values may be zero or negative.

    Binance premium-index klines are OHLC-shaped but are not prices. Reusing the
    positive-price contract parser would incorrectly reject valid negative
    premium values.
    """
    bars: list[MarketBar] = []
    seen: set[int] = set()
    previous = -1
    for row in archive.rows:
        if len(row) < 7:
            raise ArchiveError("signed kline row has fewer than seven columns")
        try:
            open_time = int(row[0])
            close_time = int(row[6])
            bar = MarketBar(
                open_time=open_time,
                close_time=close_time,
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
            )
        except (TypeError, ValueError) as exc:
            raise ArchiveError("invalid signed kline row") from exc
        if open_time in seen:
            raise ArchiveError(f"duplicate signed kline open_time {open_time}")
        if open_time <= previous:
            raise ArchiveError("signed kline rows are not strictly chronological")
        if bar.high < max(bar.open, bar.close) or bar.low > min(bar.open, bar.close):
            raise ArchiveError("invalid signed kline OHLC geometry")
        if close_time < open_time:
            raise ArchiveError("signed kline close_time precedes open_time")
        seen.add(open_time)
        previous = open_time
        bars.append(bar)
    return tuple(bars)
