"""
Backfill historical funding rates into the funding_rates table so the
funding filter can run in backtests.

Defaults to every symbol in coin_pool.json plus the majors; ~3 rows/day/symbol
(8h settlements), so a year is ~1,100 rows per symbol.

Usage (from repo root, mind DB_PATH):
    python -m scripts.backfill_funding                        # pool + majors, 1 year
    python -m scripts.backfill_funding --symbol BTCUSDT
    python -m scripts.backfill_funding --months 24
"""

from __future__ import annotations

import argparse
import json
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.data.binance_client import BinanceClient
from app.data.repository import save_funding_rates
from app.db.migrations import run_migrations

MAJORS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill Binance funding rates")
    parser.add_argument("--symbol", action="append", default=None,
                        help="Symbol(s) to backfill (default: coin_pool.json + majors)")
    parser.add_argument("--months", type=int, default=12)
    args = parser.parse_args()

    run_migrations()

    if args.symbol:
        symbols = [s.upper() for s in args.symbol]
    else:
        try:
            with open("coin_pool.json") as f:
                symbols = [s.upper() for s in json.load(f)["pool"]]
        except OSError:
            symbols = []
        symbols = list(dict.fromkeys(MAJORS + symbols))

    end_ms   = int(time.time() * 1000)
    start_ms = end_ms - args.months * 30 * 24 * 3_600_000

    print(f"Backfilling funding rates for {len(symbols)} symbol(s), {args.months} months")
    with BinanceClient() as client:
        for sym in symbols:
            try:
                rows = client.get_funding_history(sym, start_ms, end_ms)
                n = save_funding_rates(sym, rows)
                print(f"  {sym:<12} {n} rows")
            except Exception as exc:
                print(f"  {sym:<12} FAILED: {exc}")
    print("Done.")


if __name__ == "__main__":
    main()
