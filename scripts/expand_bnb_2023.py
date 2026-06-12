import sys, io, datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
from app.backtesting.engine import run_backtest

SYMBOL = "BNBUSDT"; CAPITAL = 10_000.0
RUNS = [
    ("BNB Jan-Apr 2023", "2023-01-01", "2023-04-30"),
    ("BNB Nov-Dec 2023", "2023-11-01", "2023-12-31"),
    ("BNB Jan-Mar 2024", "2024-01-01", "2024-03-31"),
    ("BNB Apr-Jun 2024", "2024-04-01", "2024-06-30"),
    ("BNB Jul-Sep 2024", "2024-07-01", "2024-09-30"),
    ("BNB Oct-Dec 2024", "2024-10-01", "2024-12-31"),
    ("BNB Jan-Mar 2025", "2025-01-01", "2025-03-31"),
    ("BNB Apr-Jun 2025", "2025-04-01", "2025-06-30"),
]
for label, s, e in RUNS:
    start_ms = int(datetime.datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
    end_ms   = int(datetime.datetime.strptime(e, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
    try:
        res = run_backtest(SYMBOL, start_ms, end_ms, CAPITAL, 1.0)
        m = res["metrics"]
        print(f"  {label:22s}: {m['total_trades']:3d} trades  WR={m['win_rate']:.0%}  {'+'if m['net_profit']>=0 else ''}${m['net_profit']:,.0f}", flush=True)
    except Exception as ex:
        print(f"  {label:22s}: ERROR — {ex}", flush=True)
print("Done.", flush=True)
