"""No-network verification for checksum-verified Binance archive support."""

from __future__ import annotations

import csv
import hashlib
import io
import zipfile

from app.research.binance_archive import (
    ArchiveDataset,
    ArchiveError,
    archive_url,
    continuity_report,
    download_verified_archive,
    parse_book_quotes,
    parse_funding_events,
    parse_kline_bars,
)

failures = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global failures
    status = "PASS" if condition else "FAIL"
    if not condition:
        failures += 1
    print(f"  [{status}] {name}{(' — ' + detail) if detail else ''}")


def zip_csv(filename: str, rows: list[list[object]]) -> bytes:
    stream = io.StringIO()
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerows(rows)
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(filename, stream.getvalue().encode())
    return payload.getvalue()


class FakeResponse:
    def __init__(self, *, content: bytes = b"", text: str | None = None, status_code: int = 200):
        self.content = content
        self.text = text if text is not None else content.decode(errors="replace")
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx
            request = httpx.Request("GET", "https://example.invalid")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("fake error", request=request, response=response)


class FakeClient:
    def __init__(self, url: str, payload: bytes, checksum_override: str | None = None):
        self.url = url
        self.payload = payload
        digest = checksum_override or hashlib.sha256(payload).hexdigest()
        self.checksum = f"{digest}  {url.rsplit('/', 1)[-1]}\n"

    def get(self, url: str):
        if url == self.url + ".CHECKSUM":
            return FakeResponse(text=self.checksum)
        if url == self.url:
            return FakeResponse(content=self.payload)
        return FakeResponse(status_code=404)


print("\n1. URL construction")
kl_url = archive_url(ArchiveDataset.KLINES, "BTCUSDT", "2026-08-31", interval="1h", cadence="daily")
check("daily kline path", kl_url.endswith("/daily/klines/BTCUSDT/1h/BTCUSDT-1h-2026-08-31.zip"), kl_url)
mark_url = archive_url(ArchiveDataset.MARK_PRICE, "XAUUSDT", "2026-08", interval="1h", cadence="monthly")
check("monthly mark path", mark_url.endswith("/monthly/markPriceKlines/XAUUSDT/1h/XAUUSDT-1h-2026-08.zip"), mark_url)
fund_url = archive_url(ArchiveDataset.FUNDING_RATE, "BTCUSDT", "2026-08", cadence="monthly")
check("funding monthly path", fund_url.endswith("/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2026-08.zip"), fund_url)
book_url = archive_url(ArchiveDataset.BOOK_TICKER, "BTCUSDT", "2024-03-29", cadence="daily")
check("bookTicker daily path", book_url.endswith("/daily/bookTicker/BTCUSDT/BTCUSDT-bookTicker-2024-03-29.zip"), book_url)

print("\n2. Checksum verification + kline parsing")
k_rows = [
    [0, "100", "101", "99", "100.5", "1", 999, "", "", "", "", ""],
    [1000, "100.5", "102", "100", "101", "1", 1999, "", "", "", "", ""],
    [2000, "101", "103", "100", "102", "1", 2999, "", "", "", "", ""],
]
k_payload = zip_csv("BTCUSDT-1h-2026-08-31.csv", k_rows)
archive = download_verified_archive(kl_url, client=FakeClient(kl_url, k_payload))
bars = parse_kline_bars(archive)
check("three klines parsed", len(bars) == 3)
continuity = continuity_report(bars, 1000)
check("synthetic kline continuity complete", continuity.complete, str(continuity))
try:
    download_verified_archive(kl_url, client=FakeClient(kl_url, k_payload, checksum_override="0" * 64))
except ArchiveError:
    bad_checksum_rejected = True
else:
    bad_checksum_rejected = False
check("checksum mismatch rejected", bad_checksum_rejected)

print("\n3. Funding parsing")
f_rows = [
    ["calc_time", "funding_interval_hours", "last_funding_rate"],
    [1000, 8, "0.0001"],
    [2000, 8, "-0.0002"],
]
f_payload = zip_csv("BTCUSDT-fundingRate-2026-08.csv", f_rows)
f_archive = download_verified_archive(fund_url, client=FakeClient(fund_url, f_payload))
funding = parse_funding_events(f_archive)
check("two funding events parsed", len(funding) == 2)
check("funding sign preserved", funding[0].rate > 0 and funding[1].rate < 0)

print("\n4. Historical bookTicker parsing")
b_rows = [
    ["update_id", "best_bid_price", "best_bid_qty", "best_ask_price", "best_ask_qty", "transaction_time", "event_time"],
    [2, "100.00", "3", "100.02", "4", 2000, 2000],
    [1, "99.99", "2", "100.01", "5", 1000, 1000],
]
b_payload = zip_csv("BTCUSDT-bookTicker-2024-03-29.csv", b_rows)
b_archive = download_verified_archive(book_url, client=FakeClient(book_url, b_payload))
quotes = parse_book_quotes(b_archive)
check("book quotes parsed", len(quotes) == 2)
check("historical unordered quotes normalized", [q.timestamp for q in quotes] == [1000, 2000])
check("bid/ask preserved", quotes[0].bid == 99.99 and quotes[0].ask == 100.01)

print(f"\n{'ALL CHECKS PASSED' if failures == 0 else f'{failures} CHECK(S) FAILED'}")
raise SystemExit(1 if failures else 0)
