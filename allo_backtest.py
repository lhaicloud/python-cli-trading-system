"""ALLOUSDT Cross-Asset Validation — 4 scenarios (newer coin, data from ~2023)."""
import sys, io, time, datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from dotenv import load_dotenv; load_dotenv()
from app.db.migrations import run_migrations; run_migrations()
from app.backtesting.engine import run_backtest
from app.utils.timeframes import str_to_ms
from app.data.repository import get_candles

SYMBOL   = "ALLOUSDT"
CAPITAL  = 10_000.0
RISK_PCT = 1.0

# Check actual data range before defining scenarios
df = get_candles(SYMBOL, "1d", limit=2000)
if df.empty:
    print("ERROR: No ALLOUSDT data in DB"); exit(1)

first_date = df.index[0].strftime('%Y-%m-%d')
last_date  = df.index[-1].strftime('%Y-%m-%d')
print(f"\n  ALLOUSDT data range: {first_date} → {last_date}  ({len(df)} daily candles)")

SCENARIOS = [
    ("ALLO LAUNCH    Q4-2025", "2025-11-15", "2025-12-31"),  # first weeks post-listing
    ("ALLO CRASH     Q1-2026", "2026-01-01", "2026-03-31"),  # broad crypto drawdown
    ("ALLO RECENT    Q2-2026", "2026-04-01", "2026-05-29"),  # most recent quarter
]

print(f"\n{'='*60}")
print(f"  ALLOUSDT CROSS-ASSET VALIDATION  ({len(SCENARIOS)} scenarios)")
print(f"{'='*60}")

results = []
total_start = time.time()

for label, start, end in SCENARIOS:
    print(f"\n{'='*57}")
    print(f"  {label}  [{start} to {end}]")
    print(f"{'='*57}")
    t0 = time.time()
    try:
        result = run_backtest(SYMBOL, str_to_ms(start), str_to_ms(end), CAPITAL, RISK_PCT)
        m = result["metrics"]
        trades = result["trades"]
        results.append((label, m))
        print(f"  Time: {time.time()-t0:.0f}s  |  Total trades: {m['total_trades']}")
        if m['total_trades'] > 0:
            print(f"  Win rate:      {m['win_rate']*100:.1f}%  ({m['win_count']}W / {m['loss_count']}L)")
            print(f"  Profit factor: {m['profit_factor']:.3f}")
            print(f"  Net profit:    ${m['net_profit']:,.2f}  ({m['return_pct']:.2f}%)")
            print(f"  Max drawdown:  {m['max_drawdown_pct']:.2f}%")
            print(f"\n  Last 15 trades:")
            for t in trades[-15:]:
                et = datetime.datetime.utcfromtimestamp(t['entry_time']/1000).strftime('%m/%d')
                p  = '+' if t['pnl'] >= 0 else ''
                print(f"    {et} {t['direction']:4s} entry={t['entry_price']:.4f} => {p}${t['pnl']:.0f} [{t['exit_reason']}]  conf={t['signal_confidence']:.0f}")
        else:
            print("  No trades generated")
    except Exception as e:
        print(f"  ERROR: {e}")
        import traceback; traceback.print_exc()
        results.append((label, {"total_trades":0,"net_profit":0,"win_rate":0,"profit_factor":0,"return_pct":0,"max_drawdown_pct":0}))

print(f"\n\n{'='*60}")
print(f"  ALLOUSDT SUMMARY")
print(f"{'='*60}")
for label, m in results:
    if m['total_trades'] > 0:
        tag = "[PROFIT]" if m['net_profit'] > 0 else "[LOSS]"
        sign = '+' if m['net_profit'] >= 0 else '-'
        print(f"  {label:<28}: {sign}${abs(m['net_profit']):>7,.0f}  ({m['win_rate']*100:.1f}% WR, PF={m['profit_factor']:.2f}, {m['total_trades']}t)  {tag}")
    else:
        print(f"  {label:<28}: no trades  [SKIP]")

total_pnl  = sum(m['net_profit'] for _, m in results)
profitable = sum(1 for _, m in results if m['total_trades'] > 0 and m['net_profit'] > 0)
with_trades= sum(1 for _, m in results if m['total_trades'] > 0)
sign = '+' if total_pnl >= 0 else '-'
print(f"\n  {profitable}/{with_trades} profitable  |  Total: {sign}${abs(total_pnl):,.2f}")
print(f"  Total time: {(time.time()-total_start)/60:.1f} min\n\nDone.")
