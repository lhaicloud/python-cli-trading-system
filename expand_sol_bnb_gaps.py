"""SOL Q2 2024 and BNB Q3 2021 gap expansion."""
import datetime
from app.backtesting.engine import run_backtest

CAPITAL = 10_000.0
RUNS = [
    ("SOLUSDT", "SOL Apr-Jun 2024", "2024-04-01", "2024-06-30"),
    ("BNBUSDT", "BNB Jul-Sep 2021", "2021-07-01", "2021-09-30"),
]
for symbol, label, s, e in RUNS:
    start_ms = int(datetime.datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
    end_ms   = int(datetime.datetime.strptime(e, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
    try:
        res = run_backtest(symbol, start_ms, end_ms, CAPITAL, 1.0)
        m = res["metrics"]
        sign = "+" if m["net_profit"] >= 0 else ""
        print(f"  {label}: {m['total_trades']} trades  WR={m['win_rate']:.0%}  {sign}${m['net_profit']:,.0f}", flush=True)
    except Exception as ex:
        print(f"  {label}: ERROR -- {ex}", flush=True)
print("Done.")
