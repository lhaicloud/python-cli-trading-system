"""
Handoff script: waits for all currently open paper trades to close,
then kills the active live runner and starts run_live_rotation.py.

Usage:
    python run_handoff.py
    python run_handoff.py --capital 10000 --risk 1.0
    python run_handoff.py --dry-run
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DB_PATH   = "data/db/lqmtf.db"
PIDS_FILE = "live_pids.json"
CHECK_INTERVAL = 30  # seconds between DB checks


def ts():
    return datetime.now().strftime("%H:%M:%S")


def get_open_trades():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, symbol, direction, entry_price, open_time FROM paper_trades WHERE status='open' ORDER BY id"
    ).fetchall()
    conn.close()
    return rows


def kill_current_runner():
    if not os.path.exists(PIDS_FILE):
        print(f"[{ts()}] No {PIDS_FILE} found — nothing to kill.")
        return
    with open(PIDS_FILE) as f:
        data = json.load(f)
    pids = data.get("pids", {})
    runner = data.get("runner", "unknown")
    print(f"[{ts()}] Stopping {runner} ({len(pids)} processes)...")
    for sym, pid in pids.items():
        try:
            os.kill(pid, 15)  # SIGTERM
            print(f"  Terminated {sym} (PID {pid})")
        except (ProcessLookupError, OSError):
            print(f"  {sym} (PID {pid}) already gone")
    time.sleep(2)


def start_rotation(args):
    cmd = [sys.executable, "run_live_rotation.py", "--risk", str(args.risk)]
    if args.capital:
        cmd += ["--capital", str(args.capital)]
    if args.dry_run:
        cmd.append("--dry-run")
    print(f"[{ts()}] Starting rotation: {' '.join(cmd)}")
    subprocess.Popen(cmd)


def main():
    parser = argparse.ArgumentParser(description="Handoff: wait for open trades then rotate coins")
    parser.add_argument("--capital", type=float, default=None)
    parser.add_argument("--risk",    type=float, default=1.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("=" * 60)
    print("  LQ-MTF Handoff Monitor")
    print("  Waiting for all open trades to close...")
    print("  Then: kill current runner -> start rotation")
    print("=" * 60)
    print()

    # Show what we're watching
    open_trades = get_open_trades()
    if not open_trades:
        print(f"[{ts()}] No open trades — starting rotation immediately.")
        kill_current_runner()
        start_rotation(args)
        return

    watched_ids = {t['id'] for t in open_trades}
    print(f"[{ts()}] Watching {len(watched_ids)} open trade(s):")
    for t in open_trades:
        print(f"  #{t['id']} {t['symbol']} {t['direction']} @ {t['entry_price']}  (opened {t['open_time']})")
    print()

    try:
        while True:
            open_now = get_open_trades()
            still_open = {t['id'] for t in open_now if t['id'] in watched_ids}

            closed = watched_ids - still_open
            for cid in sorted(closed):
                print(f"[{ts()}] Trade #{cid} CLOSED.")

            watched_ids = still_open

            if not watched_ids:
                print()
                print(f"[{ts()}] All trades closed. Switching to rotation coins...")
                print()
                kill_current_runner()
                time.sleep(1)
                start_rotation(args)
                print(f"[{ts()}] Rotation started. Handoff complete.")
                break

            remaining = len(watched_ids)
            print(f"[{ts()}] {remaining} trade(s) still open — checking again in {CHECK_INTERVAL}s...")
            time.sleep(CHECK_INTERVAL)

    except KeyboardInterrupt:
        print("\nHandoff cancelled.")


if __name__ == "__main__":
    main()
