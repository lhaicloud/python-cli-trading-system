import datetime
from app.backtesting.engine import run_backtest

SYMBOL = "BNBUSDT"; CAPITAL = 10_000.0
RUNS = [
    ("BNB Jan-Apr 2021", "2021-01-01", "2021-04-30"),
    ("BNB May-Jun 2021", "2021-05-01", "2021-06-30"),
    ("BNB Nov-Dec 2021", "2021-11-01", "2021-12-31"),
    ("BNB Jan-Mar 2022", "2022-01-01", "2022-03-31"),
    ("BNB Jul-Sep 2022", "2022-07-01", "2022-09-30"),
    ("BNB Oct-Dec 2022", "2022-10-01", "2022-12-31"),
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
        print(f"  {label}: ERROR — {ex}")
print(f"\n  +{total} trades added")
print("Done.")
