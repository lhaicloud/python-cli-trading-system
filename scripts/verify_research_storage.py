"""No-network verification for research persistence and completed-candle backfill."""

from __future__ import annotations

import sqlite3

from app.research.data_service import (
    backfill_funding,
    backfill_price_series,
    capture_book_ticker,
    snapshot_funding_info,
    snapshot_instrument_metadata,
)
from app.research.storage import ResearchStore

failures = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global failures
    status = "PASS" if condition else "FAIL"
    if not condition:
        failures += 1
    print(f"  [{status}] {name}{(' — ' + detail) if detail else ''}")


class FakeClient:
    def get_server_time(self) -> int:
        return 150_000

    def get_exchange_info(self):
        return {
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "status": "TRADING",
                    "contractType": "PERPETUAL",
                    "baseAsset": "BTC",
                    "quoteAsset": "USDT",
                    "marginAsset": "USDT",
                    "underlyingType": "COIN",
                    "filters": [
                        {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                        {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                        {"filterType": "MIN_NOTIONAL", "notional": "5"},
                    ],
                }
            ]
        }

    def get_klines(self, **_kwargs):
        return [
            [0, "100", "101", "99", "100", "1", 59_999],
            [60_000, "100", "102", "99", "101", "1", 119_999],
            [120_000, "101", "103", "100", "102", "1", 179_999],
        ]

    get_mark_price_klines = get_klines
    get_premium_index_klines = get_klines

    def get_index_price_klines(self, **kwargs):
        return self.get_klines(**kwargs)

    def get_funding_history(self, _symbol, _start, _end):
        return [{"funding_time": 80_000, "rate": 0.0001}]

    def get_funding_info(self, _symbol=None):
        return [
            {
                "symbol": "BTCUSDT",
                "fundingIntervalHours": 8,
                "adjustedFundingRateCap": "0.0005",
                "adjustedFundingRateFloor": "-0.0005",
            }
        ]

    def get_book_ticker(self, symbol):
        return {
            "symbol": symbol,
            "bidPrice": "100.0",
            "bidQty": "2",
            "askPrice": "100.1",
            "askQty": "3",
        }


conn = sqlite3.connect(":memory:")
store = ResearchStore(conn)
client = FakeClient()

print("\n1. Versioned metadata")
saved = snapshot_instrument_metadata(client, store, observed_at_ms=123)
check("one instrument snapshot stored", saved == 1)
row = conn.execute("SELECT asset_class, tick_size FROM research_instrument_metadata").fetchone()
check("asset class persisted", row[0] == "crypto")
check("tick size persisted", row[1] == 0.1)

print("\n2. Completed-candle filtering + dedupe")
inserted = backfill_price_series(
    client,
    store,
    series_type="trade",
    symbol="BTCUSDT",
    timeframe="1m",
    start_ms=0,
    end_ms=200_000,
)
check("only two fully closed candles stored", inserted == 2, f"inserted={inserted}")
count = conn.execute("SELECT COUNT(*) FROM research_price_klines").fetchone()[0]
check("forming candle excluded", count == 2, f"count={count}")
second = backfill_price_series(
    client,
    store,
    series_type="trade",
    symbol="BTCUSDT",
    timeframe="1m",
    start_ms=0,
    end_ms=200_000,
)
check("duplicate backfill inserts nothing", second == 0, f"inserted={second}")

print("\n3. Funding persistence")
funding_count = backfill_funding(client, store, symbol="BTCUSDT", start_ms=0, end_ms=150_000)
check("funding rate stored", funding_count == 1)
info_count = snapshot_funding_info(client, store, observed_at_ms=124)
check("funding-info snapshot stored", info_count == 1)

print("\n4. High-frequency book capture")
t1 = capture_book_ticker(client, store, "BTCUSDT", captured_at_ms=500)
t2 = capture_book_ticker(client, store, "BTCUSDT", captured_at_ms=500)
check("first timestamp preserved", t1 == 500)
check("second same-ms capture made unique", t2 == 501, f"t2={t2}")
book_count = conn.execute("SELECT COUNT(*) FROM research_book_tickers").fetchone()[0]
check("both book samples persisted", book_count == 2)

print(f"\n{'ALL CHECKS PASSED' if failures == 0 else f'{failures} CHECK(S) FAILED'}")
raise SystemExit(1 if failures else 0)
