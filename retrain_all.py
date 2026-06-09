"""
Retrain ML models on expanded dataset.
Run this after new backtests complete and seed_training_data.py has been run.
Trains BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT models.
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import subprocess

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]

for symbol in SYMBOLS:
    print(f"\n{'='*50}")
    print(f"  Training {symbol} models...")
    print(f"{'='*50}")
    result = subprocess.run(
        ["python", "main.py", "train", "--symbol", symbol, "--model", "both"],
        capture_output=True, text=True, encoding='utf-8', errors='replace'
    )
    print(result.stdout)
    if result.returncode != 0:
        print(f"  ERROR: {result.stderr[:300]}")

print("\n\nAll models trained. Run validate commands to activate:")
for symbol in SYMBOLS:
    print(f"  python main.py validate --symbol {symbol} --model-version latest --model-type buy --accept")
    print(f"  python main.py validate --symbol {symbol} --model-version latest --model-type sell --accept")
