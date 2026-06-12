"""SOL expansion: 2025 H2 + 2026 Q1 (recent bull run missing entirely)"""
import datetime
from app.backtesting.engine import run_backtest

SYMBOL = "SOLUSDT"
CAPITAL = 10_000.0
RUNS = [
    ("SOL Jul-Sep 2025", "2025-07-01", "2025-09-30"),
    ("SOL Oct-Dec 2025", "2025-10-01", "2025-12-31"),
    ("SOL Jan-Mar 2026", "2026-01-01", "2026-03-31"),
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
