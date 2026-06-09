"""
Expand training data for SOL and BNB by running systematic backtests
across all available history. Goal: 500+ labeled trades per symbol.
"""
import sys, io, datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)

from app.backtesting.engine import run_backtest

CAPITAL  = 10_000.0
RISK_PCT = 1.0

# Each tuple: (symbol, label, start, end)
# SOL and BNB have data from 2021-01-01 (backfilled earlier this session)
# Running non-overlapping 4-month windows across full history
RUNS = [
    # SOL — 4-month chunks across 4 years
    ("SOLUSDT", "SOL 2021-Q1Q2",   "2021-01-01", "2021-04-30"),
    ("SOLUSDT", "SOL 2021-Q3Q4",   "2021-05-01", "2021-08-31"),
    ("SOLUSDT", "SOL 2021-Q4",     "2021-09-01", "2021-12-31"),
    ("SOLUSDT", "SOL 2022-Q1Q2",   "2022-01-01", "2022-04-30"),
    ("SOLUSDT", "SOL 2022-Q3Q4",   "2022-05-01", "2022-08-31"),
    ("SOLUSDT", "SOL 2022-Q4",     "2022-09-01", "2022-12-31"),
    ("SOLUSDT", "SOL 2023-Q1Q2",   "2023-01-01", "2023-04-30"),
    ("SOLUSDT", "SOL 2023-Q3Q4",   "2023-05-01", "2023-08-31"),
    ("SOLUSDT", "SOL 2023-Q4",     "2023-09-01", "2023-12-31"),
    ("SOLUSDT", "SOL 2024-Q1Q2",   "2024-01-01", "2024-04-30"),
    ("SOLUSDT", "SOL 2024-Q3Q4",   "2024-05-01", "2024-08-31"),
    ("SOLUSDT", "SOL 2024-Q4",     "2024-09-01", "2024-12-31"),

    # BNB — same structure
    ("BNBUSDT", "BNB 2021-Q1Q2",   "2021-01-01", "2021-04-30"),
    ("BNBUSDT", "BNB 2021-Q3Q4",   "2021-05-01", "2021-08-31"),
    ("BNBUSDT", "BNB 2021-Q4",     "2021-09-01", "2021-12-31"),
    ("BNBUSDT", "BNB 2022-Q1Q2",   "2022-01-01", "2022-04-30"),
    ("BNBUSDT", "BNB 2022-Q3Q4",   "2022-05-01", "2022-08-31"),
    ("BNBUSDT", "BNB 2022-Q4",     "2022-09-01", "2022-12-31"),
    ("BNBUSDT", "BNB 2023-Q1Q2",   "2023-01-01", "2023-04-30"),
    ("BNBUSDT", "BNB 2023-Q3Q4",   "2023-05-01", "2023-08-31"),
    ("BNBUSDT", "BNB 2023-Q4",     "2023-09-01", "2023-12-31"),
    ("BNBUSDT", "BNB 2024-Q1Q2",   "2024-01-01", "2024-04-30"),
    ("BNBUSDT", "BNB 2024-Q3Q4",   "2024-05-01", "2024-08-31"),
    ("BNBUSDT", "BNB 2024-Q4",     "2024-09-01", "2024-12-31"),

    # ETH — extra recent history for model refresh
    ("ETHUSDT", "ETH 2023-Q1Q2",   "2023-01-01", "2023-06-30"),
    ("ETHUSDT", "ETH 2023-Q3Q4",   "2023-07-01", "2023-12-31"),
    ("ETHUSDT", "ETH 2024-Q1Q2",   "2024-01-01", "2024-06-30"),
    ("ETHUSDT", "ETH 2024-Q3Q4",   "2024-07-01", "2024-12-31"),
]

trade_counts = {}
for symbol, label, start_str, end_str in RUNS:
    start_ms = int(datetime.datetime.strptime(start_str, "%Y-%m-%d")
                   .replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
    end_ms   = int(datetime.datetime.strptime(end_str, "%Y-%m-%d")
                   .replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
    try:
        res = run_backtest(symbol, start_ms, end_ms, CAPITAL, RISK_PCT)
        tc  = res["metrics"]["total_trades"]
        net = res["metrics"]["net_profit"]
        wr  = res["metrics"]["win_rate"]
        trade_counts[symbol] = trade_counts.get(symbol, 0) + tc
        sign = "+" if net >= 0 else ""
        print(f"  {label:22s}: {tc:3d} trades  WR={wr:.0%}  {sign}${net:,.0f}", flush=True)
    except Exception as e:
        print(f"  {label:22s}: ERROR — {e}", flush=True)

print("\n  Trade counts added to backtest_trades:")
for sym, count in sorted(trade_counts.items()):
    print(f"    {sym}: +{count} trades", flush=True)
print("\nDone. Run seed_training_data.py next.", flush=True)
