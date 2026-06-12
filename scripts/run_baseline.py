"""Run baseline backtest and print metrics."""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from dotenv import load_dotenv
load_dotenv()

from app.db.migrations import run_migrations
run_migrations()

from app.backtesting.engine import run_backtest
from app.utils.timeframes import str_to_ms

scenarios = [
    ("BULL 2023",     "2023-01-01", "2023-12-31"),
    ("BEAR 2022",     "2022-01-01", "2022-12-31"),
    ("SIDEWAYS 2024", "2024-01-01", "2024-06-30"),
    ("RECENT 2025",   "2025-01-01", "2025-12-31"),
]

for label, start, end in scenarios:
    print(f"\n{'='*60}")
    print(f"SCENARIO: {label}  [{start} to {end}]")
    print(f"{'='*60}")
    try:
        result = run_backtest(
            symbol="BTCUSDT",
            start_ms=str_to_ms(start),
            end_ms=str_to_ms(end),
            initial_capital=10_000.0,
            risk_pct=1.0,
        )
        m = result["metrics"]
        print(f"  Total trades:       {m['total_trades']}")
        print(f"  Win rate:           {m['win_rate']*100:.1f}%")
        print(f"  Profit factor:      {m['profit_factor']:.3f}")
        print(f"  Net profit:         ${m['net_profit']:,.2f}")
        print(f"  Return %:           {m['return_pct']:.2f}%")
        print(f"  Max drawdown:       {m['max_drawdown_pct']:.2f}%")
        print(f"  Avg win:            ${m['avg_win']:,.2f}")
        print(f"  Avg loss:           ${m['avg_loss']:,.2f}")
        print(f"  Expectancy:         ${m['expectancy']:,.2f}")
        print(f"  Max consec losses:  {m['max_consecutive_losses']}")
        print(f"  BUY win rate:       {m['buy_model_win_rate']*100:.1f}%")
        print(f"  SELL win rate:      {m['sell_model_win_rate']*100:.1f}%")
        print(f"  Avg trade dur (h):  {m['avg_trade_duration_h']:.1f}h")
    except Exception as e:
        print(f"  ERROR: {e}")
        import traceback
        traceback.print_exc()

print("\nDone.")
