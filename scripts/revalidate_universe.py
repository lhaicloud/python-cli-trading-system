"""
Backtest all DB symbols with enough 30m candles that are not excluded.
Use when the curated pool has zero v5-validated coins.

    python -m scripts.revalidate_universe              # dry list
    python -m scripts.revalidate_universe --run --limit 10
    python -m scripts.revalidate_universe --run --promote-validated
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.version import ENGINE_VERSION
from app.backtesting.engine import run_backtest
from app.config import get_settings
from app.db.connection import get_conn
from app.db.migrations import run_migrations
from app.validation import passes_backtest_validation

POOL_PATH = Path("coin_pool.json")
MIN_CANDLES = 5000


def _passed(cfg, m: dict) -> bool:
    ok, _ = passes_backtest_validation(
        cfg,
        net_profit=m.get("net_profit", 0),
        win_rate=m.get("win_rate", 0),
        total_trades=m.get("total_trades", 0),
        profit_factor=m.get("profit_factor", 0) or 0,
    )
    return ok


def _load_pool() -> list[str]:
    with POOL_PATH.open(encoding="utf-8-sig") as f:
        return [s.upper() for s in json.load(f)["pool"]]


def _save_pool(pool: list[str]) -> None:
    with POOL_PATH.open("w", encoding="utf-8") as f:
        json.dump({"_comment": "Curated pool ??? v5 validated-only rotation.", "pool": pool}, f, indent=2)
        f.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest all qualified universe symbols on current engine")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--months", type=int, default=12)
    parser.add_argument("--limit", type=int, default=0, help="Max symbols (0=all untested)")
    parser.add_argument("--force", action="store_true", help="Re-run even if v5 backtest exists")
    parser.add_argument("--promote-validated", action="store_true", help="Add passing symbols to coin_pool.json")
    args = parser.parse_args()

    run_migrations()
    cfg = get_settings()
    pool = _load_pool()

    with get_conn() as conn:
        have_data = {
            r[0] for r in conn.execute(
                "SELECT symbol FROM candles WHERE timeframe='30m' "
                "GROUP BY symbol HAVING COUNT(*) >= ?",
                (MIN_CANDLES,),
            ).fetchall()
        }
        tested = {
            r[0] for r in conn.execute(
                "SELECT DISTINCT symbol FROM backtest_runs WHERE model_version = ?",
                (ENGINE_VERSION,),
            ).fetchall()
        }

    candidates = sorted(s for s in have_data if s not in cfg.excluded_symbol_set)
    if args.force:
        todo = candidates
    else:
        todo = [s for s in candidates if s not in tested]
    if args.limit > 0:
        todo = todo[: args.limit]

    print(f"Engine: {ENGINE_VERSION}", flush=True)
    print(f"Qualified in DB: {len(candidates)}  Already tested: {len(tested & set(candidates))}", flush=True)
    print(f"To test ({len(todo)}): {todo}", flush=True)
    if not args.run:
        print("\nDry list ??? re-run with --run", flush=True)
        return

    end_ms = int(time.time() * 1000) - 24 * 3_600_000
    start_ms = end_ms - args.months * 30 * 24 * 3_600_000
    promoted: list[str] = []

    for sym in todo:
        try:
            print(f"\n=== {sym} ({args.months}m) ===", flush=True)
            m = run_backtest(sym, start_ms, end_ms)["metrics"]
            ok = _passed(cfg, m)
            print(
                f"  {sym}: trades={m.get('total_trades')} net={m.get('net_profit'):+.2f} "
                f"wr={m.get('win_rate', 0):.0%} -> {'VALIDATED' if ok else 'not validated'}",
                flush=True,
            )
            if ok and args.promote_validated and sym not in pool:
                pool.append(sym)
                promoted.append(sym)
        except Exception as exc:
            print(f"  {sym} ERROR: {exc}", flush=True)

    if promoted:
        _save_pool(pool)
        print(f"\nPromoted to pool: {promoted}", flush=True)

    print("\nUniverse scan complete.", flush=True)


if __name__ == "__main__":
    main()

