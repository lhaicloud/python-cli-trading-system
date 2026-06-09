"""
Daily rotation live paper trader — single process, threaded.

At startup picks 5 fresh A-grade coins from coin_pool.json (avoiding
yesterday's selection), then runs all 5 as live watchers inside one
process using threads.

Usage:
    python run_live_rotation.py
    python run_live_rotation.py --capital 10000 --risk 1.0
    python run_live_rotation.py --dry-run
    python run_live_rotation.py --show-today   # print today's coins and exit
"""

import argparse
import json
import os
import random
import signal
import sys
import threading
import time
from datetime import datetime, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

POOL_FILE     = "coin_pool.json"
HISTORY_FILE  = "rotation_history.json"
COINS_PER_DAY = 5


# ── helpers ───────────────────────────────────────────────────────────────────

def load_pool():
    with open(POOL_FILE) as f:
        return json.load(f)["pool"]

def load_history():
    if not os.path.exists(HISTORY_FILE):
        return []
    with open(HISTORY_FILE) as f:
        return json.load(f).get("history", [])

def save_history(history):
    with open(HISTORY_FILE, "w") as f:
        json.dump({"history": history}, f, indent=2)

def today_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def pick_today(pool, history):
    today = today_str()

    # Reuse today's selection if already picked
    for entry in history:
        if entry["date"] == today:
            return entry["coins"], False  # (coins, is_new)

    # Avoid repeating yesterday's coins
    yesterday_coins = set(history[-1]["coins"]) if history else set()
    fresh = [c for c in pool if c not in yesterday_coins]
    if len(fresh) < COINS_PER_DAY:
        fresh = pool  # fallback to full pool

    selected = random.sample(fresh, COINS_PER_DAY)

    history.append({"date": today, "coins": selected})
    history = history[-30:]
    save_history(history)

    return selected, True


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Daily rotation live paper trader")
    parser.add_argument("--capital",    type=float, default=None)
    parser.add_argument("--risk",       type=float, default=1.0)
    parser.add_argument("--dry-run",    action="store_true")
    parser.add_argument("--show-today", action="store_true", help="Print today's coins and exit")
    args = parser.parse_args()

    # DB migrations before anything else
    from app.db.migrations import run_migrations
    run_migrations()

    pool    = load_pool()
    history = load_history()
    coins, is_new = pick_today(pool, history)

    # Always include any symbol with an open paper trade, even if not in rotation
    import sqlite3
    db_path = os.path.join("data", "db", "lqmtf.db")
    open_symbols = []
    if os.path.exists(db_path):
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM paper_trades WHERE status='open'"
        ).fetchall()
        conn.close()
        open_symbols = [r[0] for r in rows if r[0] not in coins]

    all_coins = coins + open_symbols

    print("=" * 60)
    print("  LQ-MTF Daily Rotation — Live Paper Trading")
    print(f"  Date: {today_str()}  |  Risk: {args.risk}%  |  Dry-run: {args.dry_run}")
    print(f"  {'New selection' if is_new else 'Resuming today'}:")
    for i, c in enumerate(coins, 1):
        print(f"    {i}. {c}")
    if open_symbols:
        print(f"  + Open trade watchlist:")
        for c in open_symbols:
            print(f"      {c} (has open trade)")
    print("  Press Ctrl+C to stop all")
    print("=" * 60)
    print()

    if args.show_today:
        return

    # Auto-backfill any coin that has no candle data yet
    print("  Checking candle data for today's coins...")
    for sym in all_coins:
        import subprocess
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

    # Shared stop event — set by Ctrl+C handler, watched by all watcher threads
    stop_event = threading.Event()

    def handle_stop(*_):
        if not stop_event.is_set():
            print("\n\nStopping all watchers...", flush=True)
        stop_event.set()

    signal.signal(signal.SIGINT,  handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)

    # Create one LiveWatcher per coin and run each in its own thread
    from app.live.watcher import LiveWatcher

    threads = []
    for sym in all_coins:
        watcher = LiveWatcher(
            symbol=sym,
            risk_pct=args.risk,
            paper_capital=args.capital,
            dry_run=args.dry_run,
            stop_event=stop_event,
        )
        t = threading.Thread(target=watcher.run, name=sym, daemon=True)
        threads.append(t)

    for t in threads:
        t.start()
        print(f"  Started {t.name}", flush=True)
        time.sleep(0.4)  # slight stagger to avoid simultaneous DB writes at startup

    print()
    print("  All coins running. Waiting for candle closes...", flush=True)
    print()

    # Block main thread until stop_event is set (Ctrl+C)
    stop_event.wait()

    # Give threads up to 15s to finish their current cycle cleanly
    for t in threads:
        t.join(timeout=15)

    print("Done.", flush=True)


if __name__ == "__main__":
    main()
