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

from app.version import ENGINE_VERSION
from app.backtesting.engine import run_backtest
from app.config import get_settings
from app.db.connection import get_conn
from app.db.migrations import run_migrations
from app.validation import passes_backtest_validation

try:
    from scripts.auto_curate_failures import remove_failed_symbol
except ImportError:
    def remove_failed_symbol(symbol: str, dry_run: bool = False) -> bool:  # type: ignore[misc]
        return False

MAJORS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]


def _save_pool(pool: list[str]) -> None:
    with open("coin_pool.json", "w", encoding="utf-8") as f:
        json.dump(
            {"_comment": "Curated pool — v5 validated-only rotation.", "pool": pool},
            f,
            indent=2,
        )
        f.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-validate pool coins on the v2 engine")
    parser.add_argument("--run",    action="store_true", help="Execute (default: list only)")
    parser.add_argument("--months", type=int, default=6)
    parser.add_argument("--limit",  type=int, default=0, help="Max symbols this invocation (0=all)")
    parser.add_argument(
        "--symbols", nargs="*", default=None,
        help="Only these symbols (e.g. DOGEUSDT). Implies --force.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-run even if a current-engine backtest already exists",
    )
    parser.add_argument(
        "--auto-curate", action="store_true",
        help="Remove failed pool coins from coin_pool.json and add to EXCLUDED_SYMBOLS",
    )
    parser.add_argument(
        "--promote-validated", action="store_true",
        help="Add passing symbols from --symbols to coin_pool.json",
    )
    args = parser.parse_args()

    run_migrations()
    cfg = get_settings()

    with open("coin_pool.json", encoding="utf-8-sig") as f:
        pool = [s.upper() for s in json.load(f)["pool"]]
    symbols = list(dict.fromkeys(MAJORS + pool))
    symbols = [s for s in symbols if s not in cfg.excluded_symbol_set]

    if args.symbols:
        symbols = [s.upper() for s in args.symbols if s.upper() not in cfg.excluded_symbol_set]
        args.force = True

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

    if args.force:
        todo = [s for s in symbols if s in have_data]
    else:
        todo = [s for s in symbols if s not in tested and s in have_data]
    skipped = [s for s in symbols if s not in have_data]
    if args.limit > 0:
        todo = todo[: args.limit]

    print(f"Engine version: {ENGINE_VERSION}", flush=True)
    print(f"Already validated/tested: {sorted(tested & set(symbols)) or 'none'}", flush=True)
    if skipped:
        print(f"Skipped (insufficient candles): {skipped}", flush=True)
    print(f"To test ({len(todo)}): {todo}", flush=True)
    if not args.run:
        print("\nDry list only — re-run with --run to execute.", flush=True)
        return

    end_ms   = int(time.time() * 1000) - 24 * 3_600_000
    start_ms = end_ms - args.months * 30 * 24 * 3_600_000
    promoted: list[str] = []

    for sym in todo:
        try:
            print(f"\n=== {sym} ({args.months}m) ===", flush=True)
            result  = run_backtest(sym, start_ms, end_ms)
            m       = result["metrics"]
            passed, tier = passes_backtest_validation(
                cfg,
                net_profit=m.get("net_profit", 0),
                win_rate=m.get("win_rate", 0),
                total_trades=m.get("total_trades", 0),
                profit_factor=m.get("profit_factor", 0) or 0,
            )
            label = f"VALIDATED ({tier})" if passed else "not validated"
            print(f"  {sym}: trades={m.get('total_trades')} net={m.get('net_profit'):+.2f} "
                  f"wr={m.get('win_rate', 0):.0%} -> {label}",
                  flush=True)
            if args.auto_curate and not passed and sym in pool:
                remove_failed_symbol(sym)
            if args.promote_validated and passed and sym not in pool:
                pool.append(sym)
                promoted.append(sym)
                _save_pool(pool)
                print(f"  -> promoted {sym} to pool (now {len(pool)} coins)", flush=True)
        except Exception as exc:
            print(f"  {sym} FAILED: {exc}", flush=True)

    if promoted:
        _save_pool(pool)
        print(f"\nPromoted to pool: {promoted}", flush=True)

    print("\nRevalidation pass complete.", flush=True)
    if todo:
        print("Restart trading services to refresh the validated watchlist:", flush=True)
        print("  sudo systemctl restart lqmtf lqmtf-live", flush=True)


if __name__ == "__main__":
    main()
