"""
Seed feature_snapshots from existing backtest_trades.

The backtest engine stores features + pnl in backtest_trades but the trainer
reads from feature_snapshots. This script bridges the gap, giving the ML
models hundreds of labeled examples from completed backtests.

Run once:  python seed_training_data.py
"""

import json
import os
import sqlite3
import sys

DB_PATH = os.environ.get("DB_PATH", "data/db/lqmtf.db")

# Which fill model's runs to bridge into training labels. 'market' = entries
# filled at the executable price (the only honest labels); 'zone' reproduces the
# pre-2026-07-30 contaminated set and exists only for comparison.
FILL_MODEL = sys.argv[1] if len(sys.argv) > 1 else "market"
if FILL_MODEL not in ("market", "zone"):
    sys.exit(f"fill_model must be 'market' or 'zone', got {FILL_MODEL!r}")


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Seed closed trades with definitive outcomes.
    # ratchet_sl is included — with lock_r=0.10 these are small wins (+0.1R).
    # end_of_data excluded — P&L depends on random end point, not trade quality.
    #
    # Only from runs whose entries filled at an obtainable price. Runs tagged
    # fill_model='zone' booked market entries at sig.entry_price — a zone level
    # the market usually wasn't trading at — so their win/loss labels describe
    # fills that never happened (82% of paper entries to 2026-07-30 sat outside
    # the candle's traded range). Training on those teaches the model what wins
    # at an unobtainable price. See realistic_entry_fill in config.py.
    cur.execute("""
        SELECT bt.symbol, bt.entry_time, bt.pnl, bt.exit_reason, bt.features
        FROM   backtest_trades bt
        JOIN   backtest_runs r ON r.id = bt.backtest_run_id
        WHERE  bt.features IS NOT NULL
          AND  bt.pnl IS NOT NULL
          AND  bt.exit_reason IN ('sl_hit', 'tp_hit', 'timeout', 'ratchet_sl')
          AND  r.fill_model = ?
    """, (FILL_MODEL,))
    trades = cur.fetchall()
    print(f"Found {len(trades)} labeled backtest trades from "
          f"fill_model='{FILL_MODEL}' runs (sl_hit / tp_hit / timeout / ratchet_sl)")

    cur.execute(
        "SELECT fill_model, COUNT(*) FROM feature_snapshots GROUP BY fill_model"
    )
    for fm, n in cur.fetchall():
        print(f"Existing feature_snapshots  fill_model={fm or 'NULL'}: {n}")

    inserted = 0
    skipped = 0

    for row in trades:
        symbol      = row["symbol"]
        entry_time  = row["entry_time"] or 0
        pnl         = row["pnl"]
        feat_raw    = row["features"]

        try:
            features = json.loads(feat_raw) if isinstance(feat_raw, str) else feat_raw
        except Exception:
            skipped += 1
            continue

        if not isinstance(features, dict):
            skipped += 1
            continue

        outcome = "win" if pnl > 0 else "loss"

        result = conn.execute(
            """
            INSERT OR IGNORE INTO feature_snapshots
                (signal_id, symbol, timestamp, features, outcome, pnl, fill_model)
            VALUES (NULL, ?, ?, ?, ?, ?, ?)
            """,
            (symbol, entry_time, json.dumps(features), outcome, pnl, FILL_MODEL),
        )
        if result.rowcount > 0:
            inserted += 1
        else:
            skipped += 1

    conn.commit()
    conn.close()

    print(f"Seeded {inserted} snapshots  |  skipped {skipped} (bad JSON / missing data)")

    # Print outcome breakdown
    conn2 = sqlite3.connect(DB_PATH)
    cur2 = conn2.cursor()
    cur2.execute("SELECT outcome, COUNT(*) FROM feature_snapshots GROUP BY outcome")
    print("\nfeature_snapshots breakdown:")
    for outcome, cnt in cur2.fetchall():
        print(f"  {outcome}: {cnt}")

    cur2.execute("SELECT symbol, COUNT(*) FROM feature_snapshots GROUP BY symbol")
    print("\nBy symbol:")
    for sym, cnt in cur2.fetchall():
        print(f"  {sym}: {cnt}")
    conn2.close()


if __name__ == "__main__":
    main()
