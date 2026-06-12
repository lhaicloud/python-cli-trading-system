"""LINKUSDT Cross-Asset Validation — 6 scenarios across different market regimes."""
import sys, io, time, datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from dotenv import load_dotenv; load_dotenv()
from app.db.migrations import run_migrations; run_migrations()
from app.backtesting.engine import run_backtest
from app.utils.timeframes import str_to_ms

SCENARIOS = [
    ("LINK MEGA-BULL Q1-2021",  "2021-01-01", "2021-03-31"),  # LINK $11->$36
    ("LINK ATH-CRASH Q4-2021",  "2021-10-01", "2021-12-31"),  # LINK $22->$35->$20
    ("LINK BEAR     Q2-2022",   "2022-04-01", "2022-06-30"),  # LINK $16->$5.5
    ("LINK RECOVERY Q1-2023",   "2023-01-01", "2023-03-31"),  # LINK $6->$8
    ("LINK BREAKOUT Q1-2024",   "2024-01-01", "2024-03-31"),  # LINK $14->$22
    ("LINK PULLBACK Q3-2024",   "2024-07-01", "2024-09-30"),  # LINK $14->$10
]

CAPITAL  = 10_000.0
RISK_PCT = 1.0
SYMBOL   = "LINKUSDT"

print(f"\n{'='*60}")
print(f"  LINKUSDT CROSS-ASSET VALIDATION  (6 scenarios)")
print(f"{'='*60}")

results = []
total_start = time.time()

for label, start, end in SCENARIOS:
    print(f"\n{'='*57}")
    print(f"  {label}  [{start} to {end}]")
    print(f"{'='*57}")
    t0 = time.time()
    try:
        result = run_backtest(
            symbol=SYMBOL,
            start_ms=str_to_ms(start),
            end_ms=str_to_ms(end),
            initial_capital=CAPITAL,
            risk_pct=RISK_PCT,
        )
        elapsed = time.time() - t0
        m = result["metrics"]
        trades = result["trades"]
        results.append((label, m))
        print(f"  Time: {elapsed:.0f}s")
        print(f"  Total trades:    {m['total_trades']}")
        if m['total_trades'] > 0:
            print(f"  Win rate:        {m['win_rate']*100:.1f}%  ({m['win_count']}W / {m['loss_count']}L)")
            print(f"  Profit factor:   {m['profit_factor']:.3f}")
            print(f"  Net profit:      ${m['net_profit']:,.2f}  ({m['return_pct']:.2f}%)")
            print(f"  Max drawdown:    {m['max_drawdown_pct']:.2f}%")
            print(f"  Avg win:         ${m['avg_win']:,.2f}")
            print(f"  Avg loss:        ${m['avg_loss']:,.2f}")
            print(f"\n  Last 15 trades:")
            for t in trades[-15:]:
                et = datetime.datetime.utcfromtimestamp(t['entry_time']/1000).strftime('%m/%d')
                p  = '+' if t['pnl'] >= 0 else ''
                print(f"    {et} {t['direction']:4s} entry={t['entry_price']:.2f} => {p}${t['pnl']:.0f} [{t['exit_reason']}]  conf={t['signal_confidence']:.0f}")
        else:
            print(f"  No trades generated")
    except Exception as e:
        print(f"  ERROR: {e}")
        import traceback; traceback.print_exc()
        results.append((label, {"total_trades": 0, "net_profit": 0, "win_rate": 0,
                                 "profit_factor": 0, "return_pct": 0, "max_drawdown_pct": 0}))

print(f"\n\n{'='*60}")
print(f"  LINKUSDT SUMMARY")
print(f"{'='*60}")
for label, m in results:
    if m['total_trades'] > 0:
        tag = "[PROFIT]" if m['net_profit'] > 0 else "[LOSS]"
        print(f"  {label:<28}: +${m['net_profit']:>7,.0f}" if m['net_profit'] >= 0
              else f"  {label:<28}: -${abs(m['net_profit']):>7,.0f}", end="")
        print(f"  ({m['win_rate']*100:.1f}% WR, PF={m['profit_factor']:.2f}, {m['total_trades']}t)  {tag}")
    else:
        print(f"  {label:<28}: no trades  [SKIP]")

total_pnl   = sum(m['net_profit'] for _, m in results)
profitable  = sum(1 for _, m in results if m['total_trades'] > 0 and m['net_profit'] > 0)
with_trades = sum(1 for _, m in results if m['total_trades'] > 0)
print(f"\n  {profitable}/{with_trades} profitable  |  Total: $+{total_pnl:,.2f}" if total_pnl >= 0
      else f"\n  {profitable}/{with_trades} profitable  |  Total: $-{abs(total_pnl):,.2f}")
print(f"  Total time: {(time.time()-total_start)/60:.1f} min")
print(f"\nDone.")
