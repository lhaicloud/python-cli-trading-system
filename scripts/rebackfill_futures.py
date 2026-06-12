"""
Re-backfill all DB symbols from scratch using the futures (fapi) data feed.
Overwrites existing spot candle data with futures candles via upsert.

Runs sequentially to respect Binance rate limits.
All 6 pipeline timeframes: 30m, 1h, 4h, 12h, 1d, 1w
"""
import sys, io, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import warnings; warnings.filterwarnings('ignore')
from dotenv import load_dotenv; load_dotenv()
from app.db.migrations import run_migrations; run_migrations()

from app.data.backfill import backfill
from app.db.connection import get_conn
from app.utils.timeframes import str_to_ms

TFS      = ["1d", "4h", "1h", "30m", "12h", "1w"]
START    = "2021-01-01"
END_MS   = int(time.time() * 1000)
START_MS = str_to_ms(START)

# Get all symbols currently in DB
with get_conn() as conn:
    symbols = [r[0] for r in conn.execute(
        "SELECT DISTINCT symbol FROM candles ORDER BY symbol"
    ).fetchall()]

print(f"Re-backfilling {len(symbols)} symbols from futures feed")
print(f"Start: {START}  |  Timeframes: {', '.join(TFS)}")
print(f"API:   https://fapi.binance.com\n")
print("=" * 60)

total_start = time.time()
results = []

for i, sym in enumerate(symbols, 1):
    print(f"\n[{i}/{len(symbols)}] {sym}")
    t0 = time.time()
    try:
        backfill(sym, TFS, START_MS, END_MS, resume=False)
        elapsed = time.time() - t0
        print(f"  Done in {elapsed:.0f}s")
        results.append((sym, True, elapsed))
    except Exception as e:
        elapsed = time.time() - t0
        print(f"  ERROR: {e}")
        results.append((sym, False, elapsed))

# Summary
total_elapsed = time.time() - total_start
ok  = sum(1 for _, s, _ in results if s)
err = sum(1 for _, s, _ in results if not s)

print(f"\n{'='*60}")
print(f"  Complete: {ok}/{len(symbols)} succeeded  |  {err} errors")
print(f"  Total time: {total_elapsed/60:.1f} min")
if err:
    print("  Errors:")
    for sym, ok, _ in results:
        if not ok:
            print(f"    {sym}")
print()
