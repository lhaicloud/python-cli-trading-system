"""Range loaders built on checksum-verified Binance Vision archives."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import httpx

from app.research.backtester import FundingEvent, MarketBar
from app.research.binance_archive import (
    ArchiveDataset,
    ArchiveError,
    archive_url,
    continuity_report,
    download_verified_archive,
    merge_bars,
    parse_funding_events,
    parse_kline_bars,
)


@dataclass(frozen=True)
class LoadedHistory:
    symbol: str
    dataset: ArchiveDataset
    bars: tuple[MarketBar, ...]
    source_hashes: tuple[str, ...]


@dataclass(frozen=True)
class LoadedFunding:
    symbol: str
    events: tuple[FundingEvent, ...]
    source_hashes: tuple[str, ...]


def month_range(start_ym: str, end_ym: str) -> tuple[str, ...]:
    """Inclusive YYYY-MM month range."""
    sy, sm = (int(part) for part in start_ym.split("-"))
    ey, em = (int(part) for part in end_ym.split("-"))
    if not (1 <= sm <= 12 and 1 <= em <= 12):
        raise ValueError("invalid month")
    if (sy, sm) > (ey, em):
        raise ValueError("start_ym must be <= end_ym")
    out: list[str] = []
    y, m = sy, sm
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1
    return tuple(out)


def _interval_ms(interval: str) -> int:
    unit = interval[-1]
    value = int(interval[:-1])
    multipliers = {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}
    if unit not in multipliers or value <= 0:
        raise ValueError(f"unsupported interval {interval!r}")
    return value * multipliers[unit]


def load_monthly_bars(
    *,
    symbol: str,
    dataset: ArchiveDataset,
    interval: str,
    start_ym: str,
    end_ym: str,
    client: httpx.Client | None = None,
) -> LoadedHistory:
    if dataset not in {
        ArchiveDataset.KLINES,
        ArchiveDataset.MARK_PRICE,
        ArchiveDataset.INDEX_PRICE,
        ArchiveDataset.PREMIUM_INDEX,
    }:
        raise ValueError("dataset is not a kline archive family")
    owns = client is None
    http = client or httpx.Client(timeout=60.0, follow_redirects=True)
    parts: list[tuple[MarketBar, ...]] = []
    hashes: list[str] = []
    try:
        for month in month_range(start_ym, end_ym):
            url = archive_url(dataset, symbol, month, interval=interval, cadence="monthly")
            archive = download_verified_archive(url, client=http)
            bars = parse_kline_bars(archive)
            if not bars:
                raise ArchiveError(f"empty {dataset.value} archive for {symbol} {month}")
            parts.append(bars)
            hashes.append(archive.sha256)
    finally:
        if owns:
            http.close()

    merged = merge_bars(parts)
    report = continuity_report(merged, _interval_ms(interval))
    if not report.complete:
        raise ArchiveError(
            f"{dataset.value} continuity failed for {symbol} {start_ym}..{end_ym}: {report}"
        )
    return LoadedHistory(symbol.upper(), dataset, merged, tuple(hashes))


def load_monthly_funding(
    *,
    symbol: str,
    start_ym: str,
    end_ym: str,
    client: httpx.Client | None = None,
) -> LoadedFunding:
    owns = client is None
    http = client or httpx.Client(timeout=60.0, follow_redirects=True)
    events_by_time: dict[int, FundingEvent] = {}
    hashes: list[str] = []
    try:
        for month in month_range(start_ym, end_ym):
            url = archive_url(ArchiveDataset.FUNDING_RATE, symbol, month, cadence="monthly")
            archive = download_verified_archive(url, client=http, max_uncompressed_bytes=32 * 1024 * 1024)
            for event in parse_funding_events(archive):
                existing = events_by_time.get(event.timestamp)
                if existing is not None and existing.rate != event.rate:
                    raise ArchiveError(f"conflicting funding event at {event.timestamp}")
                events_by_time[event.timestamp] = event
            hashes.append(archive.sha256)
    finally:
        if owns:
            http.close()
    events = tuple(events_by_time[key] for key in sorted(events_by_time))
    if not events:
        raise ArchiveError(f"no funding events for {symbol} {start_ym}..{end_ym}")
    return LoadedFunding(symbol.upper(), events, tuple(hashes))


def attach_mark_prices(
    funding: Iterable[FundingEvent],
    mark_bars: Iterable[MarketBar],
) -> tuple[FundingEvent, ...]:
    """Attach the latest mark bar price known at each funding timestamp."""
    marks = sorted(mark_bars, key=lambda b: b.open_time)
    events = sorted(funding, key=lambda f: f.timestamp)
    out: list[FundingEvent] = []
    mark_idx = -1
    for event in events:
        while mark_idx + 1 < len(marks) and marks[mark_idx + 1].open_time <= event.timestamp:
            mark_idx += 1
        if mark_idx < 0 or marks[mark_idx].close_time < event.timestamp - 3_600_000:
            raise ArchiveError(f"no fresh mark price for funding event {event.timestamp}")
        out.append(FundingEvent(event.timestamp, event.rate, marks[mark_idx].close))
    return tuple(out)
