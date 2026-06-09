"""
BNBUSDT Cross-Asset Validation — 6 scenarios across different market regimes.
First test on BNB — never in training data.
"""
import sys, io, datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from dotenv import load_dotenv; load_dotenv()
from app.db.migrations import run_migrations; run_migrations()
from app.backtesting.engine import run_backtest

SCENARIOS = [
    ("BNB MEGA-BULL Q1-2021",  "2021-01-01", "2021-03-31"),  # BNB $40->$340
    ("BNB ATH-CRASH Q4-2021",  "2021-10-01", "2021-12-31"),  # BNB $400->$700->$450
    ("BNB BEAR Q2-2022",       "2022-04-01", "2022-06-30"),  # BNB $430->$180
    ("BNB RECOVERY Q1-2023",   "2023-01-01", "2023-03-31"),  # BNB $220->$320
    ("BNB BREAKOUT Q1-2024",   "2024-01-01", "2024-03-31"),  # BNB $250->$640
    ("BNB PULLBACK Q3-2024",   "2024-07-01", "2024-09-30"),  # BNB sideways/decline
]

CAPITAL  = 10_000.0
RISK_PCT = 1.0
SYMBOL   = "BNBUSDT"

print(f"\n{'='*60}")
print(f"  BNBUSDT CROSS-ASSET VALIDATION  (6 scenarios)")
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
print(f"  BNBUSDT SUMMARY")
print(f"{'='*60}")
total_pnl = sum(r[1] for r in results if r[1] is not None)
profitable = sum(1 for r in results if r[1] is not None and r[1] > 0)
for label, pnl, wr, pf, tc in results:
    if pnl is not None:
        sign = '+' if pnl >= 0 else ''
        flag = "PROFIT" if pnl > 0 else "LOSS"
        print(f"  {label:30s}: {sign}${pnl:,.0f} ({wr:.1%} WR, PF={pf:.2f}, {tc}t)  [{flag}]")
    else:
        print(f"  {label:30s}: ERROR")
print(f"\n  {profitable}/6 profitable  |  Total: ${total_pnl:+,.2f}")
print("Done.")
