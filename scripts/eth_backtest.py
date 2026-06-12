"""ETH cross-asset validation — tests same strategy on ETHUSDT."""
import sys, io, time, datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from dotenv import load_dotenv; load_dotenv()
from app.db.migrations import run_migrations; run_migrations()
from app.backtesting.engine import run_backtest
from app.utils.timeframes import str_to_ms

scenarios = [
    ("ETH MEGA-BULL  2021-Q1", "ETHUSDT", "2021-01-01", "2021-03-31"),  # $730->$2000
    ("ETH ATH-TOP    2021-Q4", "ETHUSDT", "2021-10-01", "2021-12-31"),  # $3k->$4.8k->$3.6k
    ("ETH BEAR       2022-Q2", "ETHUSDT", "2022-04-01", "2022-06-30"),  # $3k->$880
    ("ETH SIDEWAYS   2023-Q1", "ETHUSDT", "2023-01-01", "2023-03-31"),  # $1200->$1900
    ("ETH BULL       2024-Q1", "ETHUSDT", "2024-01-01", "2024-03-31"),  # $2200->$3900
    ("ETH SIDEWAYS   2024-Q3", "ETHUSDT", "2024-07-01", "2024-09-30"),  # $3400->$2300
    ("ETH CRASH      2025-Q1", "ETHUSDT", "2025-01-01", "2025-03-31"),  # $3300->$1800
]

results = []
total_start = time.time()

for label, symbol, start, end in scenarios:
    print(f"\n{'='*57}")
    print(f"  {label}  [{start} to {end}]")
    print(f"{'='*57}")
    t0 = time.time()
    try:
        result = run_backtest(
            symbol=symbol,
            start_ms=str_to_ms(start),
            end_ms=str_to_ms(end),
            initial_capital=10_000.0,
            risk_pct=1.0,
        )
        elapsed = time.time() - t0
        m = result["metrics"]
        trades = result["trades"]
        results.append((label, m))
        print(f"  Time: {elapsed:.0f}s")
        print(f"  Trades:          {m['total_trades']}")
        if m['total_trades'] > 0:
            print(f"  Win rate:        {m['win_rate']*100:.1f}%  ({m['win_count']}W / {m['loss_count']}L)")
            print(f"  Profit factor:   {m['profit_factor']:.3f}")
            print(f"  Net profit:      ${m['net_profit']:,.2f}  ({m['return_pct']:.2f}%)")
            print(f"  Max drawdown:    {m['max_drawdown_pct']:.2f}%")
            print(f"  Avg win:         ${m['avg_win']:,.2f}")
            print(f"  Avg loss:        ${m['avg_loss']:,.2f}")
            print(f"  Expectancy:      ${m['expectancy']:,.2f}")
            print(f"  Max consec loss: {m['max_consecutive_losses']}")
            print(f"\n  Last 10 trades:")
            for t in trades[-10:]:
                et = datetime.datetime.utcfromtimestamp(t['entry_time']/1000).strftime('%m/%d')
                p = '+' if t['pnl'] >= 0 else ''
                print(f"    {et} {t['direction']:4s} {t['entry_price']:.0f} => {p}${t['pnl']:.0f} [{t['exit_reason']}] conf={t['signal_confidence']:.0f}")
        else:
            print(f"  No trades generated")
    except Exception as e:
        print(f"  ERROR: {e}")
        import traceback; traceback.print_exc()

print(f"\n\n{'='*57}")
print(f"  ETH CROSS-ASSET SUMMARY")
print(f"{'='*57}")
print(f"  {'Scenario':<26} {'Trades':>6} {'Win%':>6} {'PF':>6} {'Net%':>8} {'MaxDD':>7}")
print(f"  {'-'*26} {'-'*6} {'-'*6} {'-'*6} {'-'*8} {'-'*7}")
for label, m in results:
    short = label[:26]
    if m['total_trades'] > 0:
        print(f"  {short:<26} {m['total_trades']:>6} {m['win_rate']*100:>5.1f}% {m['profit_factor']:>6.3f} {m['return_pct']:>+7.2f}% {m['max_drawdown_pct']:>6.2f}%")
    else:
        print(f"  {short:<26} {'0':>6} {'—':>6} {'—':>6} {'—':>8} {'—':>7}")

total_pnl = sum(m['net_profit'] for _, m in results if m['total_trades'] > 0)
profitable = sum(1 for _, m in results if m['total_trades'] > 0 and m['net_profit'] > 0)
total = sum(1 for _, m in results if m['total_trades'] > 0)
print(f"\n  Total net P&L:   ${total_pnl:,.2f}")
print(f"  Profitable:      {profitable}/{total} scenarios")
print(f"  Total time:      {(time.time()-total_start)/60:.1f} min")
print(f"\nDone.")
