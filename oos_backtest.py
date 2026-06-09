"""
Out-of-sample validation — periods NOT in the training data.

Training data covers scenarios up to Q1-2025 (March 2025).
These scenarios test Q2-2025 through Q1-2026 which were NEVER backtested.
"""
import sys, io, datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from app.backtesting.engine import run_backtest
from app.utils.timeframes import dt_to_ms

SCENARIOS = [
    ("Q2-2025 POST-ATH RECOVERY", "2025-04-01", "2025-06-30"),
    ("Q3-2025 MID-YEAR",          "2025-07-01", "2025-09-30"),
    ("Q4-2025 YEAR-END",          "2025-10-01", "2025-12-31"),
    ("Q1-2026 NEW YEAR",          "2026-01-01", "2026-03-31"),
]

CAPITAL    = 10_000.0
RISK_PCT   = 1.0
SYMBOL     = "BTCUSDT"

for label, start_str, end_str in SCENARIOS:
    start_ms = int(datetime.datetime.strptime(start_str, "%Y-%m-%d").replace(
                   tzinfo=datetime.timezone.utc).timestamp() * 1000)
    end_ms   = int(datetime.datetime.strptime(end_str,   "%Y-%m-%d").replace(
                   tzinfo=datetime.timezone.utc).timestamp() * 1000)

    sep = "=" * 55
    print(f"\n{sep}")
    print(f"  {label}  [{start_str} to {end_str}]")
    print(sep)

    try:
        result  = run_backtest(SYMBOL, start_ms, end_ms, CAPITAL, RISK_PCT)
        metrics = result["metrics"]
        trades  = result["trades"]

        wins   = sum(1 for t in trades if t["pnl"] > 0)
        losses = sum(1 for t in trades if t["pnl"] <= 0)

        print(f"  Total trades:    {metrics['total_trades']}")
        print(f"  Win rate:        {metrics['win_rate']:.1%}  ({wins}W / {losses}L)")
        print(f"  Profit factor:   {metrics.get('profit_factor', 0):.3f}")
        print(f"  Net profit:      ${metrics['net_profit']:,.2f}  ({metrics['net_profit']/CAPITAL*100:.2f}%)")
        print(f"  Max drawdown:    {metrics.get('max_drawdown_pct', 0):.2f}%")

        print(f"\n  Last 10 trades:")
        for t in trades[-10:]:
            import datetime as dt2
            d = dt2.datetime.utcfromtimestamp(t['entry_time']/1000).strftime('%m/%d')
            sign = '+' if t['pnl'] > 0 else ''
            print(f"    {d} {t['direction']:4s} entry={t['entry_price']:,.0f} => "
                  f"{sign}${t['pnl']:,.0f} [{t['exit_reason']}]  conf={t['signal_confidence']:.0f}")

    except Exception as exc:
        print(f"  ERROR: {exc}")

print("\nDone.")
