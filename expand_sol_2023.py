"""SOL expansion: 2023 fill (only 45 snapshots currently)"""
import datetime
from app.backtesting.engine import run_backtest

SYMBOL = "SOLUSDT"
CAPITAL = 10_000.0
RUNS = [
    ("SOL Jan-Mar 2023", "2023-01-01", "2023-03-31"),
    ("SOL Apr-Jun 2023", "2023-04-01", "2023-06-30"),
    ("SOL Jul-Sep 2023", "2023-07-01", "2023-09-30"),
    ("SOL Oct-Dec 2023", "2023-10-01", "2023-12-31"),
]
total = 0
for label, s, e in RUNS:
    start_ms = int(datetime.datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
    end_ms   = int(datetime.datetime.strptime(e, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
    try:
        res = run_backtest(SYMBOL, start_ms, end_ms, CAPITAL, 1.0)
        m = res["metrics"]; total += m["total_trades"]
        sign = "+" if m["net_profit"] >= 0 else ""
        print(f"  {label}: {m['total_trades']} trades  WR={m['win_rate']:.0%}  {sign}${m['net_profit']:,.0f}", flush=True)
    except Exception as ex:
        print(f"  {label}: ERROR -- {ex}", flush=True)
print(f"\n  +{total} trades added")
print("Done.")
