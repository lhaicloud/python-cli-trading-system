"""Expand BNB training data — sequential, no stdout wrapper."""
import datetime
from app.backtesting.engine import run_backtest

SYMBOL = "BNBUSDT"
CAPITAL = 10_000.0
RUNS = [
    ("BNB Jan-Apr 2021", "2021-01-01", "2021-04-30"),
    ("BNB May-Jun 2021", "2021-05-01", "2021-06-30"),
    ("BNB Nov-Dec 2021", "2021-11-01", "2021-12-31"),
    ("BNB Jan-Mar 2022", "2022-01-01", "2022-03-31"),
    ("BNB Apr-Jun 2022", "2022-04-01", "2022-06-30"),
    ("BNB Jul-Sep 2022", "2022-07-01", "2022-09-30"),
    ("BNB Oct-Dec 2022", "2022-10-01", "2022-12-31"),
    ("BNB Jan-Apr 2023", "2023-01-01", "2023-04-30"),
    ("BNB Nov-Dec 2023", "2023-11-01", "2023-12-31"),
    ("BNB Jan-Mar 2024", "2024-01-01", "2024-03-31"),
    ("BNB Apr-Jun 2024", "2024-04-01", "2024-06-30"),
    ("BNB Jul-Sep 2024", "2024-07-01", "2024-09-30"),
    ("BNB Oct-Dec 2024", "2024-10-01", "2024-12-31"),
    ("BNB Jan-Mar 2025", "2025-01-01", "2025-03-31"),
    ("BNB Apr-Jun 2025", "2025-04-01", "2025-06-30"),
]

total = 0
for label, s, e in RUNS:
    start_ms = int(datetime.datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
    end_ms   = int(datetime.datetime.strptime(e, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
    try:
        res = run_backtest(SYMBOL, start_ms, end_ms, CAPITAL, 1.0)
        m = res["metrics"]
        tc = m["total_trades"]
        total += tc
        sign = "+" if m["net_profit"] >= 0 else ""
        print(f"  {label}: {tc} trades  WR={m['win_rate']:.0%}  {sign}${m['net_profit']:,.0f}")
    except Exception as ex:
        print(f"  {label}: ERROR — {ex}")

print(f"\n  Total new BNB trades: {total}")
print("Done.")
