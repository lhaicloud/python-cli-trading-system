"""Real, read-only Binance Vision integration smoke test.

This deliberately uses fixed historical archive periods so the result is
reproducible and does not depend on today's partially published files. It uses
no credentials and verifies every downloaded ZIP against Binance's published
SHA-256 CHECKSUM before parsing.
"""

from __future__ import annotations

from app.research.archive_parsers import parse_signed_kline_bars
from app.research.binance_archive import (
    ArchiveDataset,
    archive_url,
    continuity_report,
    download_verified_archive,
    parse_book_quotes,
    parse_funding_events,
    parse_kline_bars,
)


def fetch_bars(dataset: ArchiveDataset, symbol: str, period: str = "2026-08-31"):
    url = archive_url(dataset, symbol, period, interval="1h", cadence="daily")
    archive = download_verified_archive(url, max_uncompressed_bytes=64 * 1024 * 1024)
    bars = (
        parse_signed_kline_bars(archive)
        if dataset is ArchiveDataset.PREMIUM_INDEX
        else parse_kline_bars(archive)
    )
    continuity = continuity_report(bars, 3_600_000)
    if len(bars) < 20:
        raise RuntimeError(f"{dataset.value} {symbol}: unexpectedly few 1h bars ({len(bars)})")
    if not continuity.complete:
        raise RuntimeError(f"{dataset.value} {symbol}: continuity failure {continuity}")
    print(f"PASS {dataset.value:20s} {symbol:10s} rows={len(bars):4d} sha256={archive.sha256[:16]}…")
    return bars


print("Binance Vision checksum-verified integration smoke test")
for symbol in ("BTCUSDT", "XAUUSDT"):
    trade = fetch_bars(ArchiveDataset.KLINES, symbol)
    mark = fetch_bars(ArchiveDataset.MARK_PRICE, symbol)
    index = fetch_bars(ArchiveDataset.INDEX_PRICE, symbol)
    premium = fetch_bars(ArchiveDataset.PREMIUM_INDEX, symbol)
    common = set(b.open_time for b in trade)
    for name, series in (("mark", mark), ("index", index), ("premium", premium)):
        overlap = common.intersection(b.open_time for b in series)
        if len(overlap) < 20:
            raise RuntimeError(f"{symbol}: insufficient trade/{name} timestamp overlap")

    funding_url = archive_url(
        ArchiveDataset.FUNDING_RATE,
        symbol,
        "2026-08",
        cadence="monthly",
    )
    funding_archive = download_verified_archive(funding_url, max_uncompressed_bytes=16 * 1024 * 1024)
    funding = parse_funding_events(funding_archive)
    if not funding:
        raise RuntimeError(f"{symbol}: funding archive parsed no events")
    print(f"PASS {'fundingRate':20s} {symbol:10s} rows={len(funding):4d} sha256={funding_archive.sha256[:16]}…")

# Historical L1 proof: this exact USD-M archive family contains event-level
# best bid/ask changes. A fixed ETH date is used because the file is known to
# predate the later archive-coverage limitations.
book_url = archive_url(
    ArchiveDataset.BOOK_TICKER,
    "ETHUSDT",
    "2024-01-15",
    cadence="daily",
)
book_archive = download_verified_archive(book_url, max_uncompressed_bytes=512 * 1024 * 1024)
quotes = parse_book_quotes(book_archive)
if len(quotes) < 100:
    raise RuntimeError(f"ETHUSDT bookTicker: unexpectedly few quotes ({len(quotes)})")
if any(q.ask < q.bid for q in quotes):
    raise RuntimeError("ETHUSDT bookTicker: crossed invalid quote")
print(f"PASS {'bookTicker':20s} {'ETHUSDT':10s} rows={len(quotes):4d} sha256={book_archive.sha256[:16]}…")

print("ALL LIVE ARCHIVE CHECKS PASSED")
