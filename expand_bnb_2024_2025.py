"""BNB expansion: Q4 2024 + 2025 full year (major bull run data missing)"""
import datetime
from app.backtesting.engine import run_backtest

SYMBOL = "BNBUSDT"
CAPITAL = 10_000.0
RUNS = [
    ("BNB Oct-Dec 2024", "2024-10-01", "2024-12-31"),
    ("BNB Jan-Mar 2025", "2025-01-01", "2025-03-31"),
    ("BNB Apr-Jun 2025", "2025-04-01", "2025-06-30"),
    ("BNB Jul-Sep 2025", "2025-07-01", "2025-09-30"),
    ("BNB Oct-Dec 2025", "2025-10-01", "2025-12-31"),
]
total = 0
for label, s, e in RUNS:
    start_ms = int(datetime.datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
    end_ms   = int(datetime.datetime.strptime(e, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
    try:
        res = run_backtest(SYMBOL, start_ms, end_ms, CAPITAL, 1.0)
        m = res["metrics"]; total += m["total_trades"]
        sign = "+" if m["net_profit"] >= 0 else ""
        print(f"  {label}: {m['total_trades']} trades  WR={m['win_rate']:.0%}  {sign}${m['net_profit']:,.0f}")
    except Exception as ex:
        print(f"  {label}: ERROR -- {ex}")
print(f"\n  +{total} trades added")
print("Done.")
