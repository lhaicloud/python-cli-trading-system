"""Update all DB symbols with latest candles from futures (fapi) feed."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import warnings; warnings.filterwarnings('ignore')
from dotenv import load_dotenv; load_dotenv()
from app.db.migrations import run_migrations; run_migrations()
from app.data.backfill import update_latest
from app.db.connection import get_conn

TFS = ["1d", "4h", "1h", "30m", "12h", "1w"]

with get_conn() as conn:
    symbols = [r[0] for r in conn.execute(
        "SELECT DISTINCT symbol FROM candles ORDER BY symbol"
    ).fetchall()]

print(f"Updating {len(symbols)} symbols from futures feed...\n")
for sym in symbols:
    print(f"  {sym}...", end=" ", flush=True)
    try:
        counts = update_latest(sym, TFS)
        total  = sum(counts.values())
        print(f"+{total} candles")
    except Exception as e:
        print(f"ERROR: {e}")

print("\nDone.")
