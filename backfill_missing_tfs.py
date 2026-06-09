"""Backfill missing 12h and 1w data for symbols that only have 30m/1h/4h/1d."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from dotenv import load_dotenv; load_dotenv()
from app.db.migrations import run_migrations; run_migrations()

import warnings; warnings.filterwarnings('ignore')

import time
from app.data.backfill import backfill
from app.utils.timeframes import str_to_ms

SYMBOLS  = ["AAVEUSDT", "ADAUSDT", "DOGEUSDT", "OPUSDT", "TIAUSDT", "TRXUSDT", "UNIUSDT"]
TFS      = ["12h", "1w"]
START    = "2021-01-01"
END_MS   = int(time.time() * 1000)

print(f"Backfilling {TFS} for {len(SYMBOLS)} symbols from {START}...")
print()

for sym in SYMBOLS:
    print(f"=== {sym} ===")
    try:
        reports = backfill(sym, TFS, str_to_ms(START), END_MS)
        for tf, r in reports.items():
            n = r.get("candles_stored", 0)
            print(f"  {tf}: {n:,} candles stored")
    except Exception as e:
        print(f"  ERROR: {e}")
    print()

print("Done.")
