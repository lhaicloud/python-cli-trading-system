"""
Run live paper trading for all 4 symbols in parallel.
Each symbol gets its own subprocess so they track candle closes independently.

Usage:
    python run_live_paper.py
    python run_live_paper.py --capital 10000 --risk 1.0
    python run_live_paper.py --dry-run
"""
import argparse
import io
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime

# Force UTF-8 output on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
PREFIX  = {
    "BTCUSDT": "[BTC]",
    "ETHUSDT": "[ETH]",
    "SOLUSDT": "[SOL]",
    "BNBUSDT": "[BNB]",
}

def stream_output(proc, symbol):
    tag = PREFIX.get(symbol, f"[{symbol}]")
    for line in iter(proc.stdout.readline, b""):
        text = line.decode("utf-8", errors="replace").rstrip()
        # Skip pydantic warnings noise
        if not text or "pydantic" in text or "protected_namespaces" in text or "warnings.warn" in text:
            continue
        ts = datetime.now().strftime("%H:%M:%S")
        try:
            print(f"{tag} {ts}  {text}", flush=True)
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser(description="Live paper trading — all symbols")
    parser.add_argument("--capital",  type=float, default=None)
    parser.add_argument("--risk",     type=float, default=1.0)
    parser.add_argument("--dry-run",  action="store_true")
    args = parser.parse_args()

    print("=" * 60)
    print("  LQ-MTF Live Paper Trading — all symbols")
    print(f"  Risk: {args.risk}%  |  Dry-run: {args.dry_run}")
    print("  Press Ctrl+C to stop all")
    print("=" * 60)
    print()

    # Pre-flight: ensure all symbols have 12H and 1W history
    print("  Checking candle data for all symbols...")
    for sym in SYMBOLS:
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

    processes = []
    threads   = []

    for sym in SYMBOLS:
        cmd = [
            sys.executable, "main.py", "live",
            "--symbol", sym,
            "--risk", str(args.risk),
        ]
        if args.capital:
            cmd += ["--capital", str(args.capital)]
        if args.dry_run:
            cmd.append("--dry-run")

        env = {
            **os.environ,
            "PYTHONIOENCODING": "utf-8",
            # Only one child may long-poll Telegram getUpdates (409 otherwise)
            "TELEGRAM_LISTENER": "1" if sym == SYMBOLS[0] else "0",
        }
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
        )
        processes.append((sym, proc))

        t = threading.Thread(target=stream_output, args=(proc, sym), daemon=True)
        t.start()
        threads.append(t)

        print(f"  Started {sym} (PID {proc.pid})")
        time.sleep(0.5)  # stagger starts slightly

    # Write child PIDs so handoff script can terminate cleanly
    pids = {sym: proc.pid for sym, proc in processes}
    with open("live_pids.json", "w") as f:
        json.dump({"runner": "run_live_paper", "pids": pids}, f, indent=2)

    print()
    print("All symbols running. Waiting for candle closes...")
    print()

    try:
        while True:
            # Check if any process died unexpectedly
            for sym, proc in processes:
                if proc.poll() is not None:
                    print(f"\n[WARNING] {sym} process exited (code {proc.returncode}). Restarting...")
                    # Remove and restart
                    processes.remove((sym, proc))
                    cmd = [
                        sys.executable, "main.py", "live",
                        "--symbol", sym,
                        "--risk", str(args.risk),
                    ]
                    if args.capital:
                        cmd += ["--capital", str(args.capital)]
                    if args.dry_run:
                        cmd.append("--dry-run")
                    env = {
                        **os.environ,
                        "PYTHONIOENCODING": "utf-8",
                        "TELEGRAM_LISTENER": "1" if sym == SYMBOLS[0] else "0",
                    }
                    new_proc = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        env=env,
                    )
                    processes.append((sym, new_proc))
                    t = threading.Thread(target=stream_output, args=(new_proc, sym), daemon=True)
                    t.start()
                    threads.append(t)
            time.sleep(5)
    except KeyboardInterrupt:
        print("\n\nStopping all symbol watchers...")
        for sym, proc in processes:
            proc.terminate()
            print(f"  Stopped {sym}")
        for sym, proc in processes:
            proc.wait()
        print("Done.")


if __name__ == "__main__":
    main()
