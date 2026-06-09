"""BNB expansion: May-Oct 2023 gap (completely missing from training data)"""
import datetime
from app.backtesting.engine import run_backtest

SYMBOL = "BNBUSDT"
CAPITAL = 10_000.0
RUNS = [
    ("BNB May-Jun 2023", "2023-05-01", "2023-06-30"),
    ("BNB Jul-Sep 2023", "2023-07-01", "2023-09-30"),
    ("BNB Oct 2023",     "2023-10-01", "2023-10-31"),
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
