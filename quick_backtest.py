"""Quick backtest — single quarter — to measure cycle-1 improvement."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from dotenv import load_dotenv; load_dotenv()
from app.db.migrations import run_migrations; run_migrations()

from app.backtesting.engine import run_backtest
from app.utils.timeframes import str_to_ms

scenarios = [
    ("BULL Q1-2023",      "2023-01-01", "2023-03-31"),
    ("BEAR Q2-2022",      "2022-04-01", "2022-06-30"),
    ("SIDEWAYS Q3-2024",  "2024-07-01", "2024-09-30"),
    ("RECENT Q1-2025",    "2025-01-01", "2025-03-31"),
]

for label, start, end in scenarios:
    print(f"\n{'='*55}")
    print(f"  {label}  [{start} to {end}]")
    print(f"{'='*55}")
    try:
        result = run_backtest(
            symbol="BTCUSDT",
            start_ms=str_to_ms(start),
            end_ms=str_to_ms(end),
            initial_capital=10_000.0,
            risk_pct=1.0,
        )
        m = result["metrics"]
        trades = result["trades"]
        print(f"  Total trades:    {m['total_trades']}")
        if m['total_trades'] > 0:
            print(f"  Win rate:        {m['win_rate']*100:.1f}%  ({m['win_count']}W / {m['loss_count']}L)")
            print(f"  Profit factor:   {m['profit_factor']:.3f}")
            print(f"  Net profit:      ${m['net_profit']:,.2f}  ({m['return_pct']:.2f}%)")
            print(f"  Max drawdown:    {m['max_drawdown_pct']:.2f}%")
            print(f"  Avg win:         ${m['avg_win']:,.2f}")
            print(f"  Avg loss:        ${m['avg_loss']:,.2f}")
            print(f"  Expectancy:      ${m['expectancy']:,.2f}")
            print(f"  Max consec loss: {m['max_consecutive_losses']}")
            print(f"  RR (avg win/loss): {m['avg_win']/m['avg_loss']:.2f}" if m['avg_loss'] > 0 else "  RR: N/A")
            # Show individual trades
            print(f"\n  Trades detail:")
            for t in trades[-20:]:  # last 20 trades
                import datetime
                et = datetime.datetime.utcfromtimestamp(t['entry_time']/1000).strftime('%m/%d')
                pnl_sign = '+' if t['pnl'] >= 0 else ''
                print(f"    {et} {t['direction']:4s} entry={t['entry_price']:.0f} sl={t['stop_loss']:.0f} tp={t['take_profit']:.0f} => {pnl_sign}${t['pnl']:.0f} [{t['exit_reason']}]  conf={t['signal_confidence']:.0f}")
        else:
            print(f"  No trades generated")
    except Exception as e:
        print(f"  ERROR: {e}")
        import traceback; traceback.print_exc()

print("\nDone.")
