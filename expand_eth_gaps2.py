"""ETH critical gap expansion: missing/sparse quarters."""
import datetime
from app.backtesting.engine import run_backtest

SYMBOL = "ETHUSDT"
CAPITAL = 10_000.0
RUNS = [
    ("ETH Apr-Jun 2021", "2021-04-01", "2021-06-30"),
    ("ETH Jul-Sep 2021", "2021-07-01", "2021-09-30"),
    ("ETH Jan-Mar 2022", "2022-01-01", "2022-03-31"),
    ("ETH Jul-Sep 2023", "2023-07-01", "2023-09-30"),
    ("ETH Apr-Jun 2024", "2024-04-01", "2024-06-30"),
    ("ETH Oct-Dec 2024", "2024-10-01", "2024-12-31"),
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
