"""Restart lqmtf services when a v5-validated coin appears; add it to coin_pool.

Run in background during pool revalidation:

    nohup python -m scripts.restart_on_validate >> /tmp/restart_on_validate.log 2>&1 &
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from app.config import get_settings
from app.db.connection import get_conn
from app.db.migrations import run_migrations
from app.validation import passes_backtest_validation
from app.version import ENGINE_VERSION

FLAG = "/tmp/restart_on_first_validate.done"
POOL_PATH = Path("coin_pool.json")
POLL_S = 120


def _validated_symbols() -> list[str]:
    cfg = get_settings()
    out: list[str] = []
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT symbol, win_rate, total_trades, net_profit, profit_factor
            FROM backtest_runs
            WHERE model_version = ?
            ORDER BY symbol, created_at DESC
            """,
            (ENGINE_VERSION,),
        ).fetchall()
    seen: set[str] = set()
    for row in rows:
        sym = row[0]
        if sym in seen:
            continue
        seen.add(sym)
        ok, _ = passes_backtest_validation(
            cfg,
            net_profit=row[3] or 0,
            win_rate=row[1] or 0,
            total_trades=row[2] or 0,
            profit_factor=row[4] or 0,
        )
        if ok:
            out.append(sym)
    return sorted(out)


def _revalidate_running() -> bool:
    try:
        out = subprocess.check_output(["pgrep", "-f", "revalidate_pool.*--run"], text=True)
        return bool(out.strip())
    except subprocess.CalledProcessError:
        return False


def _promote_to_pool(symbols: list[str]) -> list[str]:
    with POOL_PATH.open(encoding="utf-8-sig") as f:
        data = json.load(f)
    pool = [s.upper() for s in data["pool"]]
    added = [s for s in symbols if s not in pool]
    if not added:
        return []
    pool.extend(added)
    data["pool"] = pool
    with POOL_PATH.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    return added


def main() -> None:
    import os

    run_migrations()
    print(f"Watching for validated {ENGINE_VERSION} coins (poll={POLL_S}s)", flush=True)
    idle_polls = 0

    while True:
        validated = _validated_symbols()
        running = _revalidate_running()

        if validated and not os.path.exists(FLAG):
            added = _promote_to_pool(validated)
            if added:
                print(f"Promoted to coin_pool: {added}", flush=True)
            print(f"Validated={validated} ??? restarting lqmtf + lqmtf-live", flush=True)
            subprocess.run(["systemctl", "restart", "lqmtf", "lqmtf-live"], check=False)
            with open(FLAG, "w", encoding="utf-8") as f:
                f.write(",".join(validated))
            break

        if not running:
            idle_polls += 1
            # Keep waiting a few cycles in case a chained batch starts (universe queue)
            if idle_polls >= 3:
                print(
                    f"No revalidation running and 0 validated ??? watcher exiting "
                    f"(validated={validated or 'none'})",
                    flush=True,
                )
                break
        else:
            idle_polls = 0

        time.sleep(POLL_S)


if __name__ == "__main__":
    main()

