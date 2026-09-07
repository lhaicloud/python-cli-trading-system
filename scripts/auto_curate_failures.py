"""
Remove pool coins that failed the current engine's validation gates and add
them to EXCLUDED_SYMBOLS.  Run once after revalidation, or in --watch mode
during a long revalidate_pool batch.

    python -m scripts.auto_curate_failures          # one-shot
    python -m scripts.auto_curate_failures --watch  # poll every 120s
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.config import get_settings
from app.db.connection import get_conn
from app.db.migrations import run_migrations
from app.validation import passes_backtest_validation
from app.version import ENGINE_VERSION

POOL_PATH = Path("coin_pool.json")
CONFIG_PATH = Path("app/config.py")
POLL_S = 120


def _passed(cfg, row) -> bool:
    ok, _ = passes_backtest_validation(
        cfg,
        net_profit=row["net_profit"] or 0,
        win_rate=row["win_rate"] or 0,
        total_trades=row["total_trades"] or 0,
        profit_factor=row.get("profit_factor") or 0,
    )
    return ok


def _latest_backtests(symbols: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    with get_conn() as conn:
        for sym in symbols:
            row = conn.execute(
                """
                SELECT win_rate, total_trades, net_profit, profit_factor, created_at
                FROM backtest_runs
                WHERE symbol=? AND model_version=?
                ORDER BY created_at DESC LIMIT 1
                """,
                (sym, ENGINE_VERSION),
            ).fetchone()
            if row:
                out[sym] = {
                    "win_rate": row[0],
                    "total_trades": row[1],
                    "net_profit": row[2],
                    "profit_factor": row[3],
                    "created_at": row[4],
                }
    return out


def _load_pool() -> list[str]:
    with POOL_PATH.open() as f:
        return [s.upper() for s in json.load(f)["pool"]]


def _save_pool(pool: list[str]) -> None:
    data = {
        "_comment": "Curated pool ??? rotation uses validated-only on current engine (v5 regimegate).",
        "pool": pool,
    }
    with POOL_PATH.open("w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def _add_to_excluded(symbol: str) -> bool:
    """Append symbol to excluded_symbols default in config.py. Returns True if changed."""
    text = CONFIG_PATH.read_text(encoding="utf-8")
    if symbol in text:
        return False
    m = re.search(
        r'("LTCUSDT,OPUSDT,JUPUSDT,ADAUSDT(?:,AVAXUSDT)?(?:,[A-Z0-9]+)*)"',
        text,
    )
    if not m:
        # Fallback: append before closing quote on excluded_symbols second line
        m = re.search(r'("LTCUSDT,OPUSDT,JUPUSDT,ADAUSDT[^"]*)"', text)
    if not m:
        print(f"  WARN: could not patch config.py for {symbol}", flush=True)
        return False
    old = m.group(1)
    if symbol in old:
        return False
    new = f'{old},{symbol}"'
    CONFIG_PATH.write_text(text.replace(f'{old}"', new, 1), encoding="utf-8")
    return True


def remove_failed_symbol(symbol: str, dry_run: bool = False) -> bool:
    """Remove one symbol from pool and add to EXCLUDED_SYMBOLS. Returns True if changed."""
    symbol = symbol.upper()
    pool = _load_pool()
    if symbol not in pool:
        return False
    if dry_run:
        print(f"  would remove {symbol} from pool", flush=True)
        return True
    pool = [s for s in pool if s != symbol]
    _save_pool(pool)
    _add_to_excluded(symbol)
    print(f"  curated: removed {symbol} from pool -> {len(pool)} coins left", flush=True)
    return True


def curate_once(dry_run: bool = False) -> list[str]:
    run_migrations()
    cfg = get_settings()
    pool = _load_pool()
    removed: list[str] = []

    backtests = _latest_backtests(pool)
    for sym in list(pool):
        row = backtests.get(sym)
        if not row:
            continue
        if _passed(cfg, row):
            print(
                f"  KEEP {sym}: WR={row['win_rate']:.0%} n={row['total_trades']} "
                f"net=${row['net_profit']:+.0f}",
                flush=True,
            )
            continue
        print(
            f"  REMOVE {sym}: WR={row['win_rate']:.0%} n={row['total_trades']} "
            f"net=${row['net_profit']:+.0f} [{row['created_at']}]",
            flush=True,
        )
        removed.append(sym)
        if dry_run:
            continue
        pool = [s for s in pool if s != sym]
        _add_to_excluded(sym)

    if removed and not dry_run:
        _save_pool(pool)
        print(f"\nCurated: removed {removed} -> pool={pool}", flush=True)
    elif not removed:
        print("No failures to curate.", flush=True)
    return removed


def main() -> None:
    parser = argparse.ArgumentParser(description="Auto-remove failed pool coins")
    parser.add_argument("--watch", action="store_true", help=f"Poll every {POLL_S}s")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print(f"Engine: {ENGINE_VERSION}", flush=True)
    if args.watch:
        print(f"Watching (poll={POLL_S}s)...", flush=True)
        while True:
            curate_once(dry_run=args.dry_run)
            time.sleep(POLL_S)
    else:
        curate_once(dry_run=args.dry_run)


if __name__ == "__main__":
    main()

