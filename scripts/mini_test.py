"""Quick 1-month test to verify Cycle 1+2+3 changes are working."""
import sys, io, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from dotenv import load_dotenv; load_dotenv()
from app.db.migrations import run_migrations; run_migrations()

from app.backtesting.engine import run_backtest
from app.utils.timeframes import str_to_ms

t0 = time.time()
print("Running 1-month bull market test (Jan 2023)...")
try:
    result = run_backtest(
        symbol="BTCUSDT",
        start_ms=str_to_ms("2023-01-01"),
        end_ms=str_to_ms("2023-01-31"),
        initial_capital=10_000.0,
        risk_pct=1.0,
    )
    elapsed = time.time() - t0
    m = result["metrics"]
    print(f"Done in {elapsed:.1f}s")
    print(f"  Total trades:    {m['total_trades']}")
    if m['total_trades'] > 0:
        print(f"  Win rate:        {m['win_rate']*100:.1f}%")
        print(f"  Profit factor:   {m['profit_factor']:.3f}")
        print(f"  Net profit:      ${m['net_profit']:,.2f}")
        print(f"  Max drawdown:    {m['max_drawdown_pct']:.2f}%")
        print(f"  Expectancy:      ${m['expectancy']:,.2f}")
        for t in result["trades"]:
            import datetime
            et = datetime.datetime.utcfromtimestamp(t['entry_time']/1000).strftime('%m/%d %H:%M')
            p = '+' if t['pnl'] >= 0 else ''
            print(f"    {et} {t['direction']} entry={t['entry_price']:.0f} => {p}${t['pnl']:.0f} [{t['exit_reason']}] conf={t['signal_confidence']:.0f}")
    else:
        print("  No trades generated")
except Exception as e:
    print(f"ERROR: {e}")
    import traceback; traceback.print_exc()
