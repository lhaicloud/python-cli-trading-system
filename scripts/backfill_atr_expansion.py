"""
Retroactively compute atr_expansion for existing feature_snapshots.
Loads all candles per symbol at once for efficiency.
"""
import json
import numpy as np
import pandas as pd
from app.db.connection import get_conn
from app.data.repository import get_candles

ATR_PERIOD   = 14
ROLL_WINDOW  = 50


def compute_atr_expansion_series(df: pd.DataFrame) -> pd.Series:
    """Returns a Series of atr_expansion indexed by open_time (ms)."""
    if df.empty:
        return pd.Series(dtype=float)
    high, low, close = df["high"], df["low"], df["close"]
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(span=ATR_PERIOD, adjust=False).mean()
    atr_roll = atr.rolling(ROLL_WINDOW, min_periods=5).mean()
    expansion = (atr / atr_roll).fillna(1.0).clip(0.1, 10.0).round(4)
    # Index is open_time (ms) when returned by get_candles
    return expansion


print("Loading all feature_snapshots...")
with get_conn() as conn:
    snaps = conn.execute("""
        SELECT id, symbol, timestamp, features
        FROM feature_snapshots
        ORDER BY symbol, timestamp
    """).fetchall()

print(f"Total: {len(snaps)} snapshots")

# Group by symbol
from collections import defaultdict
by_symbol = defaultdict(list)
for row in snaps:
    by_symbol[row["symbol"]].append(row)

updated = 0
already_set = 0
errors = 0
updates_to_apply = []

for symbol, rows in by_symbol.items():
    timestamps = [r["timestamp"] for r in rows]
    min_ts = min(timestamps)
    max_ts = max(timestamps)
    # Load candles with lookback for rolling window
    lookback_extra = ROLL_WINDOW * 30 * 60 * 1000
    df = get_candles(symbol, "30m",
                     start_ms=min_ts - lookback_extra,
                     end_ms=max_ts + 60_000)

    if df is None or df.empty:
        print(f"  {symbol}: no candle data found, skipping")
        errors += len(rows)
        continue

    expansion_series = compute_atr_expansion_series(df)

    print(f"  {symbol}: {len(rows)} snapshots, {len(df)} candles")

    for row in rows:
        snap_id  = row["id"]
        ts       = row["timestamp"]
        try:
            feats = json.loads(row["features"])
        except Exception:
            errors += 1
            continue

        if "atr_expansion" in feats and feats["atr_expansion"] != 1.0:
            already_set += 1
            continue

        # Convert ms timestamp to UTC datetime for DatetimeIndex lookup
        ts_dt = pd.Timestamp(ts, unit="ms", tz="UTC")
        idx = expansion_series.index.searchsorted(ts_dt, side="right") - 1
        if idx < 0:
            val = 1.0
        else:
            val = float(expansion_series.iloc[idx])

        feats["atr_expansion"] = val
        updates_to_apply.append((json.dumps(feats), snap_id))
        updated += 1

print(f"\nApplying {len(updates_to_apply)} updates...")
with get_conn() as conn:
    for i in range(0, len(updates_to_apply), 200):
        batch = updates_to_apply[i:i+200]
        conn.executemany(
            "UPDATE feature_snapshots SET features=? WHERE id=?",
            batch,
        )
        conn.commit()
        print(f"  Committed batch {i//200 + 1}/{(len(updates_to_apply)+199)//200}")

print(f"\nDone.")
print(f"  Updated:     {updated:,}")
print(f"  Already set: {already_set:,}")
print(f"  Errors:      {errors:,}")
