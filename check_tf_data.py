"""Check which timeframes have data in the DB."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from app.db.migrations import run_migrations; run_migrations()
from app.data.repository import get_candles
from app.utils.timeframes import str_to_ms

symbols = ["BTCUSDT", "ETHUSDT"]
timeframes = ["30m", "1h", "4h", "12h", "1d", "1w"]
date_ranges = [
    ("2022-12-01", "2023-04-01"),
    ("2020-12-01", "2021-04-01"),
]

for sym in symbols:
    print(f"\n=== {sym} ===")
    for start, end in date_ranges:
        print(f"  Period: {start} to {end}")
        for tf in timeframes:
            df = get_candles(sym, tf, start_ms=str_to_ms(start), end_ms=str_to_ms(end))
            if df.empty:
                print(f"    {tf:5s}: EMPTY — NO DATA")
            else:
                print(f"    {tf:5s}: {len(df):4d} candles  [{df.index[0].date()} → {df.index[-1].date()}]")
