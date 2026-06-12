"""
Re-validate the rotation pool against the CURRENT backtest engine.

Old backtest_runs were produced by the optimistic v1 engine (inflated profit
factors) and no longer count toward scanner validation. This runs a fresh
backtest for every coin_pool.json symbol (plus the majors) that has no
ENGINE_VERSION run yet, so `select_watchlist(validated_only=True)` has an
honest basis.

Run on the live host at deploy (or locally with DB_PATH=data/db/lqmtf_dev.db
for a preview). Long-running: ~5-10 min per symbol for a 6-month window.

Usage:
    python -m scripts.revalidate_pool                 # list what would run
    python -m scripts.revalidate_pool --run           # actually run
    python -m scripts.revalidate_pool --run --months 6 --limit 3
"""

from __future__ import annotations

import argparse
import json
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.backtesting.engine import ENGINE_VERSION, run_backtest
from app.config import get_settings
from app.db.connection import get_conn
from app.db.migrations import run_migrations

MAJORS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-validate pool coins on the v2 engine")
    parser.add_argument("--run",    action="store_true", help="Execute (default: list only)")
    parser.add_argument("--months", type=int, default=6)
    parser.add_argument("--limit",  type=int, default=0, help="Max symbols this invocation (0=all)")
    args = parser.parse_args()

    run_migrations()
    cfg = get_settings()

    with open("coin_pool.json") as f:
        pool = [s.upper() for s in json.load(f)["pool"]]
    symbols = list(dict.fromkeys(MAJORS + pool))

    with get_conn() as conn:
        tested = {
            r[0] for r in conn.execute(
                "SELECT DISTINCT symbol FROM backtest_runs WHERE model_version = ?",
                (ENGINE_VERSION,),
            ).fetchall()
        }
        have_data = {
            r[0] for r in conn.execute(
                "SELECT symbol FROM candles WHERE timeframe='30m' "
                "GROUP BY symbol HAVING COUNT(*) >= 5000"
            ).fetchall()
        }

    todo = [s for s in symbols if s not in tested and s in have_data]
    skipped = [s for s in symbols if s not in have_data]
    if args.limit > 0:
        todo = todo[: args.limit]

    print(f"Engine version: {ENGINE_VERSION}")
    print(f"Already validated/tested: {sorted(tested & set(symbols)) or 'none'}")
    if skipped:
        print(f"Skipped (insufficient candles): {skipped}")
    print(f"To test ({len(todo)}): {todo}")
    if not args.run:
        print("\nDry list only — re-run with --run to execute.")
        return

    end_ms   = int(time.time() * 1000) - 24 * 3_600_000
    start_ms = end_ms - args.months * 30 * 24 * 3_600_000

    for sym in todo:
        try:
            print(f"\n=== {sym} ({args.months}m) ===")
            result  = run_backtest(sym, start_ms, end_ms)
            m       = result["metrics"]
            passed = (
                m.get("net_profit", 0) > cfg.backtest_min_profit
                and m.get("win_rate", 0) >= cfg.backtest_min_win_rate
                and m.get("total_trades", 0) >= cfg.backtest_min_trades
            )
            print(f"  {sym}: trades={m.get('total_trades')} net={m.get('net_profit'):+.2f} "
                  f"wr={m.get('win_rate', 0):.0%} -> {'VALIDATED' if passed else 'not validated'}")
        except Exception as exc:
            print(f"  {sym} FAILED: {exc}")

    print("\nRevalidation pass complete.")


if __name__ == "__main__":
    main()
