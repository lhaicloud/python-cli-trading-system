"""Show BNB quarterly snapshot coverage."""
from app.db.connection import get_conn
from collections import defaultdict
from datetime import datetime, timezone

with get_conn() as conn:
    rows = conn.execute(
        "SELECT timestamp FROM feature_snapshots WHERE symbol='BNBUSDT' ORDER BY timestamp"
    ).fetchall()

buckets = defaultdict(int)
for r in rows:
    ts = r["timestamp"]
    dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
    key = f"{dt.year}-Q{(dt.month-1)//3+1}"
    buckets[key] += 1

for k in sorted(buckets):
    print(f"  {k}: {buckets[k]}")
print(f"  Total: {len(rows)}")
