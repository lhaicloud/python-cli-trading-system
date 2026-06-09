"""Show quarterly snapshot coverage for all symbols."""
from app.db.connection import get_conn
from collections import defaultdict
from datetime import datetime, timezone

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]

with get_conn() as conn:
    for sym in SYMBOLS:
        rows = conn.execute(
            "SELECT timestamp FROM feature_snapshots WHERE symbol=? ORDER BY timestamp",
            (sym,)
        ).fetchall()
        buckets = defaultdict(int)
        for r in rows:
            dt = datetime.fromtimestamp(r["timestamp"] / 1000, tz=timezone.utc)
            key = f"{dt.year}-Q{(dt.month-1)//3+1}"
            buckets[key] += 1
        print(f"\n{sym} ({len(rows)} total):")
        for k in sorted(buckets):
            bar = "#" * (buckets[k] // 3)
            print(f"  {k}: {buckets[k]:3d}  {bar}")
