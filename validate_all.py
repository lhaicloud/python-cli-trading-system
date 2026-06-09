"""Auto-validate and activate all latest candidate models."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import subprocess

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]

for symbol in SYMBOLS:
    for mtype in ["buy", "sell"]:
        print(f"\n--- Validating {symbol} {mtype.upper()} model ---")
        result = subprocess.run(
            ["python", "main.py", "validate",
             "--symbol", symbol, "--model-type", mtype,
             "--model-version", "latest", "--accept"],
            capture_output=True, text=True, encoding='utf-8', errors='replace'
        )
        print(result.stdout)
        if result.returncode != 0:
            print(f"  STDERR: {result.stderr[:200]}")

print("\nDone. All models validated.")
