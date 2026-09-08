"""Checksum-verified access to Binance's official public futures archive.

The archive is used as a second, read-only research data source so historical
research does not depend solely on live REST availability. No credentials are
required. Missing/corrupt archives fail closed.
"""

from __future__ import annotations

import csv
import hashlib
import io
import zipfile
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Sequence

import httpx

from app.research.backtester import BookQuote, FundingEvent, MarketBar


ARCHIVE_BASE = "https://data.binance.vision/data/futures/um"


class ArchiveError(RuntimeError):
    pass


class ArchiveDataset(str, Enum):
    KLINES = "klines"
    MARK_PRICE = "markPriceKlines"
    INDEX_PRICE = "indexPriceKlines"
    PREMIUM_INDEX = "premiumIndexKlines"
    FUNDING_RATE = "fundingRate"
    BOOK_TICKER = "bookTicker"


@dataclass(frozen=True)
class ArchiveSpec:
    path_segment: str
    per_interval: bool
    daily: bool
    monthly: bool
    filename_includes_segment: bool


SPECS: dict[ArchiveDataset, ArchiveSpec] = {
    ArchiveDataset.KLINES: ArchiveSpec("klines", True, True, True, False),
    ArchiveDataset.MARK_PRICE: ArchiveSpec("markPriceKlines", True, True, True, False),
    ArchiveDataset.INDEX_PRICE: ArchiveSpec("indexPriceKlines", True, True, True, False),
    ArchiveDataset.PREMIUM_INDEX: ArchiveSpec("premiumIndexKlines", True, True, True, False),
    ArchiveDataset.FUNDING_RATE: ArchiveSpec("fundingRate", False, False, True, True),
    ArchiveDataset.BOOK_TICKER: ArchiveSpec("bookTicker", False, True, False, True),
}


@dataclass(frozen=True)
class VerifiedArchive:
    url: str
    sha256: str
    member_name: str
    header: tuple[str, ...] | None
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class ContinuityReport:
    rows: int
    duplicates: int
    out_of_order: int
    missing_intervals: int

    @property
    def complete(self) -> bool:
        return self.duplicates == 0 and self.out_of_order == 0 and self.missing_intervals == 0


def archive_url(
    dataset: ArchiveDataset,
    symbol: str,
    period: str,
    *,
    interval: str | None = None,
    cadence: str,
) -> str:
    spec = SPECS[dataset]
    cadence = cadence.lower()
    if cadence not in {"daily", "monthly"}:
        raise ValueError("cadence must be daily or monthly")
    if cadence == "daily" and not spec.daily:
        raise ValueError(f"{dataset.value} does not have daily archives")
    if cadence == "monthly" and not spec.monthly:
        raise ValueError(f"{dataset.value} does not have monthly archives")
    if spec.per_interval and not interval:
        raise ValueError(f"{dataset.value} requires an interval")
    if not spec.per_interval and interval is not None:
        raise ValueError(f"{dataset.value} does not accept an interval")

    symbol = symbol.upper().strip()
    if not symbol or "/" in symbol or ".." in symbol:
        raise ValueError("invalid symbol")
    if "/" in period or ".." in period:
        raise ValueError("invalid period")

    if spec.per_interval:
        assert interval is not None
        base = f"{ARCHIVE_BASE}/{cadence}/{spec.path_segment}/{symbol}/{interval}"
        filename = f"{symbol}-{interval}-{period}.zip"
    else:
        base = f"{ARCHIVE_BASE}/{cadence}/{spec.path_segment}/{symbol}"
        filename = f"{symbol}-{spec.path_segment}-{period}.zip"
    return f"{base}/{filename}"


def _parse_checksum(text: str, expected_filename: str) -> str:
    parts = text.strip().split()
    if len(parts) < 2:
        raise ArchiveError("invalid checksum file")
    digest = parts[0].lower()
    filename = parts[-1].lstrip("*")
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise ArchiveError("invalid sha256 digest")
    if filename != expected_filename:
        raise ArchiveError(f"checksum filename mismatch: {filename!r} != {expected_filename!r}")
    return digest


def _header_and_rows(raw: bytes) -> tuple[tuple[str, ...] | None, tuple[tuple[str, ...], ...]]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ArchiveError("archive CSV is not UTF-8") from exc
    parsed = [tuple(cell.strip() for cell in row) for row in csv.reader(io.StringIO(text)) if row]
    if not parsed:
        raise ArchiveError("archive CSV is empty")
    first = parsed[0]
    try:
        float(first[0])
        has_header = False
    except (TypeError, ValueError):
        has_header = True
    return (first if has_header else None, tuple(parsed[1:] if has_header else parsed))


def download_verified_archive(
    url: str,
    *,
    client: httpx.Client | None = None,
    max_uncompressed_bytes: int = 512 * 1024 * 1024,
) -> VerifiedArchive:
    """Download ZIP + published CHECKSUM and return the single CSV member."""
    owns_client = client is None
    http = client or httpx.Client(timeout=60.0, follow_redirects=True)
    try:
        checksum_response = http.get(url + ".CHECKSUM")
        checksum_response.raise_for_status()
        expected_filename = url.rsplit("/", 1)[-1]
        expected_sha = _parse_checksum(checksum_response.text, expected_filename)

        response = http.get(url)
        response.raise_for_status()
        payload = response.content
        actual_sha = hashlib.sha256(payload).hexdigest()
        if actual_sha != expected_sha:
            raise ArchiveError(f"checksum mismatch for {expected_filename}")

        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as zf:
                members = [info for info in zf.infolist() if not info.is_dir()]
                csv_members = [info for info in members if info.filename.lower().endswith(".csv")]
                if len(csv_members) != 1:
                    raise ArchiveError(f"expected one CSV member, got {len(csv_members)}")
                info = csv_members[0]
                if info.file_size > max_uncompressed_bytes:
                    raise ArchiveError("archive exceeds uncompressed size limit")
                normalized = info.filename.replace("\\", "/")
                if normalized.startswith("/") or ".." in normalized.split("/"):
                    raise ArchiveError("unsafe archive member path")
                raw = zf.read(info)
        except zipfile.BadZipFile as exc:
            raise ArchiveError("invalid ZIP archive") from exc

        header, rows = _header_and_rows(raw)
        return VerifiedArchive(
            url=url,
            sha256=actual_sha,
            member_name=info.filename,
            header=header,
            rows=rows,
        )
    except httpx.HTTPError as exc:
        raise ArchiveError(f"archive request failed: {exc}") from exc
    finally:
        if owns_client:
            http.close()


def parse_kline_bars(archive: VerifiedArchive) -> tuple[MarketBar, ...]:
    bars: list[MarketBar] = []
    seen: set[int] = set()
    previous = -1
    for row in archive.rows:
        if len(row) < 7:
            raise ArchiveError("kline row has fewer than seven columns")
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
            raise ArchiveError("invalid kline row") from exc
        if open_time in seen:
            raise ArchiveError(f"duplicate kline open_time {open_time}")
        if open_time <= previous:
            raise ArchiveError("kline rows are not strictly chronological")
        if min(bar.open, bar.high, bar.low, bar.close) <= 0:
            raise ArchiveError("non-positive kline price")
        if bar.high < max(bar.open, bar.close) or bar.low > min(bar.open, bar.close):
            raise ArchiveError("invalid kline OHLC geometry")
        if close_time < open_time:
            raise ArchiveError("kline close_time precedes open_time")
        seen.add(open_time)
        previous = open_time
        bars.append(bar)
    return tuple(bars)


def continuity_report(bars: Sequence[MarketBar], interval_ms: int) -> ContinuityReport:
    if interval_ms <= 0:
        raise ValueError("interval_ms must be positive")
    if not bars:
        return ContinuityReport(0, 0, 0, 0)
    duplicates = 0
    out_of_order = 0
    missing = 0
    seen: set[int] = set()
    previous: int | None = None
    for bar in bars:
        if bar.open_time in seen:
            duplicates += 1
        if previous is not None:
            delta = bar.open_time - previous
            if delta <= 0:
                out_of_order += 1
            elif delta > interval_ms:
                missing += max(0, delta // interval_ms - 1)
        seen.add(bar.open_time)
        previous = bar.open_time
    return ContinuityReport(len(bars), duplicates, out_of_order, missing)


def _header_index(archive: VerifiedArchive) -> dict[str, int]:
    if archive.header is None:
        return {}
    return {name.strip().lower(): idx for idx, name in enumerate(archive.header)}


def parse_funding_events(archive: VerifiedArchive) -> tuple[FundingEvent, ...]:
    idx = _header_index(archive)
    time_idx = idx.get("calc_time", 0)
    rate_idx = idx.get("last_funding_rate", 2)
    events: list[FundingEvent] = []
    previous = -1
    for row in archive.rows:
        if len(row) <= max(time_idx, rate_idx):
            raise ArchiveError("funding row missing required columns")
        try:
            timestamp = int(row[time_idx])
            rate = float(row[rate_idx])
        except (TypeError, ValueError) as exc:
            raise ArchiveError("invalid funding row") from exc
        if timestamp <= previous:
            raise ArchiveError("funding rows are not strictly chronological")
        previous = timestamp
        events.append(FundingEvent(timestamp=timestamp, rate=rate))
    return tuple(events)


def parse_book_quotes(archive: VerifiedArchive) -> tuple[BookQuote, ...]:
    idx = _header_index(archive)
    # Binance Vision bookTicker headers are normally present; indices provide
    # a fail-closed compatible fallback for the published seven-column format.
    bid_idx = idx.get("best_bid_price", 1)
    bid_qty_idx = idx.get("best_bid_qty", 2)
    ask_idx = idx.get("best_ask_price", 3)
    ask_qty_idx = idx.get("best_ask_qty", 4)
    time_idx = idx.get("transaction_time", 5)
    quotes: list[BookQuote] = []
    for row in archive.rows:
        if len(row) <= max(bid_idx, bid_qty_idx, ask_idx, ask_qty_idx, time_idx):
            raise ArchiveError("bookTicker row missing required columns")
        try:
            quote = BookQuote(
                timestamp=int(row[time_idx]),
                bid=float(row[bid_idx]),
                ask=float(row[ask_idx]),
                bid_qty=float(row[bid_qty_idx]),
                ask_qty=float(row[ask_qty_idx]),
            )
        except (TypeError, ValueError) as exc:
            raise ArchiveError("invalid bookTicker row") from exc
        if quote.timestamp <= 0 or quote.bid <= 0 or quote.ask <= 0 or quote.ask < quote.bid:
            raise ArchiveError("invalid bookTicker values")
        quotes.append(quote)
    # Historical Binance bookTicker files have had ordering defects; sorting is
    # explicit and duplicates are preserved so callers can audit them.
    return tuple(sorted(quotes, key=lambda q: q.timestamp))


def merge_bars(parts: Iterable[Sequence[MarketBar]]) -> tuple[MarketBar, ...]:
    merged: dict[int, MarketBar] = {}
    for part in parts:
        for bar in part:
            existing = merged.get(bar.open_time)
            if existing is not None and existing != bar:
                raise ArchiveError(f"conflicting duplicate kline at {bar.open_time}")
            merged[bar.open_time] = bar
    return tuple(merged[key] for key in sorted(merged))
