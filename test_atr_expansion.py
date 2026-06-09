"""Quick sanity check: verify atr_expansion values are sensible."""
import json
from app.db.connection import get_conn

with get_conn() as conn:
    rows = conn.execute("""
        SELECT symbol, features
        FROM feature_snapshots
        ORDER BY RANDOM()
        LIMIT 100
    """).fetchall()

from collections import defaultdict
import statistics

vals = defaultdict(list)
for row in rows:
    try:
        f = json.loads(row["features"])
        v = f.get("atr_expansion", None)
        if v is not None:
            vals[row["symbol"]].append(v)
    except Exception:
        pass

print("atr_expansion sanity check (100 random samples):")
for sym, v_list in sorted(vals.items()):
    print(f"  {sym}: n={len(v_list)}  "
          f"min={min(v_list):.3f}  max={max(v_list):.3f}  "
          f"mean={statistics.mean(v_list):.3f}  "
          f"median={statistics.median(v_list):.3f}")
    below_half = sum(1 for x in v_list if x < 0.5)
    above_two  = sum(1 for x in v_list if x > 2.0)
    print(f"    <0.5: {below_half}   >2.0: {above_two}   1.0 exact: {sum(1 for x in v_list if x == 1.0)}")
