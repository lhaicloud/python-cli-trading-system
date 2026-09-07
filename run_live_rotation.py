"""
Daily rotation live paper trader ??? single multi-symbol watcher.

Coin selection is CONVICTION-BASED via the universe scanner, restricted to
the curated pool in coin_pool.json (validated coins first, padded with the
highest-conviction unvalidated pool coins). Random selection remains only
as a fallback when the scanner fails.

Rotation happens IN-PROCESS: the watcher re-runs the pool scan every
--rescan-every cycles (default 48 ?? 30m = daily), pinning symbols with open
trades and emergency-closing positions in dangerous regimes. No external
restart is needed for the daily re-pick.

rotation_history.json is written for audit only ??? the old "avoid yesterday's
coins" rule is gone: trend persistence is the edge, so winners stay until
the scanner ranks something higher.

Usage:
    python run_live_rotation.py
    python run_live_rotation.py --capital 10000 --risk 1.0
    python run_live_rotation.py --dry-run
    python run_live_rotation.py --show-today   # print today's picks and exit
"""

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

POOL_FILE     = "coin_pool.json"
HISTORY_FILE  = "rotation_history.json"
PIDS_FILE     = "live_pids.json"
COINS_PER_DAY = 5


# ?????? helpers ?????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????

def load_pool():
    with open(POOL_FILE) as f:
        return [s.upper() for s in json.load(f)["pool"]]


def today_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def record_history(coins):
    history = []
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE) as f:
            history = json.load(f).get("history", [])
    today = today_str()
    history = [h for h in history if h.get("date") != today]
    history.append({"date": today, "coins": list(coins)})
    with open(HISTORY_FILE, "w") as f:
        json.dump({"history": history[-30:]}, f, indent=2)


def pick_coins(pool, n=COINS_PER_DAY):
    """Conviction-ranked pick from the pool; random fallback only on scanner error."""
    try:
        from scan_universe import select_watchlist
        picks = select_watchlist(n=n, validated_only=True, pool=pool, verbose=True)
        if picks:
            return picks, "scanner"
        print(
            "  [Rotation] No validated pool coins ??? watchlist empty "
            "(awaiting backtests; not using random fallback)."
        )
        return [], "none"
    except Exception as exc:
        print(f"  [Rotation] Scanner failed ({exc}) ??? falling back to random pick.")
    return random.sample(pool, min(n, len(pool))), "random"


def get_open_symbols(coins):
    """Symbols with open paper trades that are not already in the watchlist."""
    import sqlite3
    db_path = os.environ.get("DB_PATH") or os.path.join("data", "db", "lqmtf.db")
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT DISTINCT symbol FROM paper_trades WHERE status='open'"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows if r[0] not in coins]


# ?????? main ??????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????

def main():
    parser = argparse.ArgumentParser(description="Daily rotation live paper trader")
    parser.add_argument("--capital",      type=float, default=None)
    parser.add_argument("--risk",         type=float, default=1.0)
    parser.add_argument("--dry-run",      action="store_true")
    parser.add_argument("--show-today",   action="store_true", help="Print today's picks and exit")
    parser.add_argument("--rescan-every", type=int, default=48,
                        help="Re-run the pool scan every N cycles (48 ?? 30m = daily)")
    args = parser.parse_args()

    # DB migrations before anything else
    from app.db.migrations import run_migrations
    run_migrations()

    pool = load_pool()
    coins, source = pick_coins(pool)
    open_symbols = get_open_symbols(coins)
    all_coins = coins + open_symbols
    record_history(coins)

    print("=" * 60)
    print("  LQ-MTF Daily Rotation ??? Live Paper Trading")
    print(f"  Date: {today_str()}  |  Risk: {args.risk}%  |  Dry-run: {args.dry_run}")
    print(f"  Selection: {source} (conviction-ranked pool scan)"
          if source == "scanner" else
          "  Selection: EMPTY (no validated coins yet)"
          if source == "none" else
          "  Selection: RANDOM FALLBACK (scanner failed)")
    for i, c in enumerate(coins, 1):
        print(f"    {i}. {c}")
    if open_symbols:
        print("  + Open trade watchlist:")
        for c in open_symbols:
            print(f"      {c} (has open trade)")
    print(f"  In-process re-pick every {args.rescan_every} cycles "
          f"(~{args.rescan_every / 2:.0f}h on 30m)")
    print("  Press Ctrl+C to stop")
    print("=" * 60)
    print()

    if args.show_today:
        return

    # Auto-backfill any coin that has no candle data yet
    print("  Checking candle data for today's coins...")
    import subprocess
    for sym in all_coins:
        result = subprocess.run(
            [sys.executable, "main.py", "backfill", "--symbol", sym,
             "--timeframes", "1d", "--timeframes", "12h",
             "--timeframes", "4h", "--timeframes", "1h",
             "--timeframes", "30m", "--timeframes", "1w"],
            capture_output=True, text=True,
        )
        downloaded = "Downloading" in result.stdout or "new candles" in result.stdout
        print(f"  {'Backfilled' if downloaded else 'Data OK'}: {sym}")
    print()

    # Record our PID so run_handoff.py can manage this runner
    with open(PIDS_FILE, "w") as f:
        json.dump({"runner": "run_live_rotation", "pids": {"main": os.getpid()}}, f, indent=2)

    # ONE multi-symbol watcher: shared PositionManager (portfolio caps work),
    # one PriceMonitor, one Telegram listener, in-process daily rescan.
    from app.live.watcher import LiveWatcher

    watcher = LiveWatcher(
        symbols=all_coins,
        risk_pct=args.risk,
        paper_capital=args.capital,
        dry_run=args.dry_run,
        rescan_every=args.rescan_every,
        rescan_n=COINS_PER_DAY,
        rescan_validated=True,
        rescan_pool=pool,
        rotation_history_file=HISTORY_FILE,
    )
    watcher.run()
    print("Done.", flush=True)


if __name__ == "__main__":
    main()

