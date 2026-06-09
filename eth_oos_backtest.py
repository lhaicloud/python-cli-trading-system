"""
ETH OOS Validation — Q2-2025 through Q1-2026
Extends ETH cross-asset testing beyond the training data (ETH training ended Q1-2025).
Runs with and without ML model for A/B comparison.
"""
import sys, io, os, datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from app.backtesting.engine import run_backtest

SCENARIOS = [
    ("ETH Q2-2025 POST-ATH", "2025-04-01", "2025-06-30"),
    ("ETH Q3-2025 MID-YEAR",  "2025-07-01", "2025-09-30"),
    ("ETH Q4-2025 YEAR-END",  "2025-10-01", "2025-12-31"),
    ("ETH Q1-2026 NEW YEAR",  "2026-01-01", "2026-03-31"),
]

CAPITAL  = 10_000.0
RISK_PCT = 1.0
SYMBOL   = "ETHUSDT"
USE_ML   = os.environ.get("DISABLE_ML_MODEL", "0") == "0"

print(f"\n{'='*60}")
print(f"  ETH OOS BACKTEST  |  ML={'ON' if USE_ML else 'OFF'}")
print(f"{'='*60}")

results = []
for label, start_str, end_str in SCENARIOS:
    start_ms = int(datetime.datetime.strptime(start_str, "%Y-%m-%d")
                   .replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
    end_ms   = int(datetime.datetime.strptime(end_str,   "%Y-%m-%d")
                   .replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)

    print(f"\n{'='*55}")
    print(f"  {label}  [{start_str} to {end_str}]")
    print(f"{'='*55}")
    try:
        res = run_backtest(SYMBOL, start_ms, end_ms, CAPITAL, RISK_PCT)
        m   = res["metrics"]
        t   = res["trades"]
        wins   = sum(1 for x in t if x["pnl"] > 0)
        losses = sum(1 for x in t if x["pnl"] <= 0)
        print(f"  Total trades:    {m['total_trades']}")
        print(f"  Win rate:        {m['win_rate']:.1%}  ({wins}W / {losses}L)")
        print(f"  Profit factor:   {m.get('profit_factor', 0):.3f}")
        print(f"  Net profit:      ${m['net_profit']:,.2f}  ({m['net_profit']/CAPITAL*100:.2f}%)")
        print(f"  Max drawdown:    {m.get('max_drawdown_pct', 0):.2f}%")
        results.append((label, m['net_profit'], m['win_rate'], m.get('profit_factor', 0), m['total_trades']))
    except Exception as e:
        print(f"  ERROR: {e}")
        results.append((label, None, None, None, None))

print(f"\n{'='*60}")
print(f"  SUMMARY  (ML={'ON' if USE_ML else 'OFF'})")
print(f"{'='*60}")
total_pnl = 0
for label, pnl, wr, pf, tc in results:
    if pnl is not None:
        sign = '+' if pnl >= 0 else ''
        print(f"  {label:30s}: {sign}${pnl:,.0f} ({wr:.1%} WR, PF={pf:.2f}, {tc}t)")
        total_pnl += pnl
    else:
        print(f"  {label:30s}: ERROR")
print(f"\n  4-scenario total: ${total_pnl:+,.2f}")
print("Done.")
