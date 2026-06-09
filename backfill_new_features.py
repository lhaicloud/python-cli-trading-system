"""
Retroactively compute ema_alignment and price_velocity for all existing
feature snapshots that are missing these values.
"""
import json
from collections import defaultdict

import pandas as pd

from app.db.connection import get_conn
from app.data.repository import get_candles
from app.ta.indicators import add_indicators

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]

print("Loading all feature_snapshots...")
with get_conn() as conn:
    snaps = conn.execute("""
        SELECT id, symbol, timestamp, features
        FROM feature_snapshots
        ORDER BY symbol, timestamp
    """).fetchall()

print(f"Total: {len(snaps)} snapshots")

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

    lookback_extra = 200 * 30 * 60 * 1000  # 200 bars of 30m lookback
    df = get_candles(symbol, "30m",
                     start_ms=min_ts - lookback_extra,
                     end_ms=max_ts + 60_000)

    if df is None or df.empty:
        print(f"  {symbol}: no candle data, skipping")
        errors += len(rows)
        continue

    df = add_indicators(df)
    df = df.sort_index()
    print(f"  {symbol}: {len(rows)} snapshots, {len(df)} candles")

    for row in rows:
        snap_id = row["id"]
        ts      = row["timestamp"]
        try:
            feats = json.loads(row["features"])
        except Exception:
            errors += 1
            continue

        # Skip if both already present
        if "ema_alignment" in feats and "price_velocity" in feats:
            already_set += 1
            continue

        snap_ts = pd.Timestamp(ts, unit="ms", tz="UTC")
        idx = df.index.searchsorted(snap_ts, side="right")
        if idx < 4:
            errors += 1
            continue

        window = df.iloc[max(0, idx - 50):idx]
        if len(window) < 4:
            errors += 1
            continue

        last = window.iloc[-1]
        # ema_alignment
        try:
            e9  = float(last["ema_9"])
            e21 = float(last["ema_21"])
            e50 = float(last["ema_50"])
            e200= float(last["ema_200"])
            feats["ema_alignment"] = float(int(e9 > e21) + int(e21 > e50) + int(e50 > e200))
        except Exception:
            feats["ema_alignment"] = 1.5

        # price_velocity
        try:
            c_now = float(window["close"].iloc[-1])
            c_3   = float(window["close"].iloc[-4])
            atr14 = float(last["atr_14"])
            if atr14 > 0:
                feats["price_velocity"] = float(max(-5.0, min(5.0, (c_now - c_3) / atr14)))
            else:
                feats["price_velocity"] = 0.0
        except Exception:
            feats["price_velocity"] = 0.0

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
