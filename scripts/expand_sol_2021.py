import sys, io, datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
from app.backtesting.engine import run_backtest

SYMBOL = "SOLUSDT"; CAPITAL = 10_000.0
# Avoid Jul-Oct 2021 (ATH chop deadlock)
RUNS = [
    ("SOL Jan-Apr 2021", "2021-01-01", "2021-04-30"),
    ("SOL May-Jun 2021", "2021-05-01", "2021-06-30"),
    ("SOL Nov-Dec 2021", "2021-11-01", "2021-12-31"),
    ("SOL Jan-Mar 2022", "2022-01-01", "2022-03-31"),
    ("SOL Apr-Jun 2022", "2022-04-01", "2022-06-30"),
    ("SOL Jul-Sep 2022", "2022-07-01", "2022-09-30"),
    ("SOL Oct-Dec 2022", "2022-10-01", "2022-12-31"),
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
