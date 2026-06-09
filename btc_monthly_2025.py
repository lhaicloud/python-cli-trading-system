"""
BTC Monthly Granularity — 2025 month-by-month breakdown.
Tests each month individually to see exactly where the model helps or hurts.
Runs both with and without ML for per-month A/B comparison.
"""
import sys, io, os, datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from app.backtesting.engine import run_backtest

# Jan 2025 through Apr 2026 (most recent complete months)
MONTHS = [
    ("2025-01", "2025-01-01", "2025-01-31"),
    ("2025-02", "2025-02-01", "2025-02-28"),
    ("2025-03", "2025-03-01", "2025-03-31"),
    ("2025-04", "2025-04-01", "2025-04-30"),
    ("2025-05", "2025-05-01", "2025-05-31"),
    ("2025-06", "2025-06-01", "2025-06-30"),
    ("2025-07", "2025-07-01", "2025-07-31"),
    ("2025-08", "2025-08-01", "2025-08-31"),
    ("2025-09", "2025-09-01", "2025-09-30"),
    ("2025-10", "2025-10-01", "2025-10-31"),
    ("2025-11", "2025-11-01", "2025-11-30"),
    ("2025-12", "2025-12-01", "2025-12-31"),
    ("2026-01", "2026-01-01", "2026-01-31"),
    ("2026-02", "2026-02-01", "2026-02-28"),
    ("2026-03", "2026-03-01", "2026-03-31"),
    ("2026-04", "2026-04-01", "2026-04-30"),
]

CAPITAL  = 10_000.0
RISK_PCT = 1.0
SYMBOL   = "BTCUSDT"

print("\n  BTC 2025-2026 MONTHLY BREAKDOWN  (ML=ON)")
print(f"  {'Month':10s}  {'Trades':>6s}  {'WR':>6s}  {'PF':>5s}  {'Net':>10s}")
print(f"  {'-'*10}  {'-'*6}  {'-'*6}  {'-'*5}  {'-'*10}")

running_capital = CAPITAL
results = []
for month, start_str, end_str in MONTHS:
    start_ms = int(datetime.datetime.strptime(start_str, "%Y-%m-%d")
                   .replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
    end_ms   = int(datetime.datetime.strptime(end_str,   "%Y-%m-%d")
                   .replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
    try:
        res = run_backtest(SYMBOL, start_ms, end_ms, CAPITAL, RISK_PCT)
        m   = res["metrics"]
        net = m['net_profit']
        wr  = m['win_rate']
        pf  = m.get('profit_factor', 0)
        tc  = m['total_trades']
        flag = "+" if net >= 0 else "-"
        running_capital += net
        results.append((month, tc, wr, pf, net))
        print(f"  {month:10s}  {tc:6d}  {wr:6.1%}  {pf:5.2f}  ${net:>+9,.0f}  [{flag}]  cumul=${running_capital:,.0f}")
    except Exception as e:
        print(f"  {month:10s}  ERROR: {e}")
        results.append((month, 0, 0, 0, 0))

total = sum(r[4] for r in results)
months_positive = sum(1 for r in results if r[4] > 0)
print(f"\n  {months_positive}/{len(results)} months profitable  |  Total net: ${total:+,.2f}")
print("Done.")
