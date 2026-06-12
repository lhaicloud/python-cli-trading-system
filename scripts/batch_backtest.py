"""
Batch backtest across validated symbols and periods.
Run: python batch_backtest.py
"""
import sys
from app.backtesting.engine import run_backtest
from app.utils.timeframes import dt_to_ms
from datetime import datetime

SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT",
    "XRPUSDT", "AVAXUSDT", "LINKUSDT", "DOGEUSDT",
    "DOTUSDT", "XLMUSDT", "ARBUSDT",
]

PERIODS = [
    ("2023-01-01", "2023-06-30", "H1 2023"),
    ("2023-07-01", "2023-12-31", "H2 2023"),
    ("2024-01-01", "2024-06-30", "H1 2024"),
    ("2024-07-01", "2024-12-31", "H2 2024"),
    ("2025-01-01", "2025-03-31", "Q1 2025"),
]

results = []

for symbol in SYMBOLS:
    for start_str, end_str, label in PERIODS:
        start_ms = int(datetime.strptime(start_str, "%Y-%m-%d").timestamp() * 1000)
        end_ms   = int(datetime.strptime(end_str,   "%Y-%m-%d").timestamp() * 1000)
        try:
            r = run_backtest(symbol, start_ms, end_ms)
            m = r["metrics"]
            results.append({
                "symbol": symbol,
                "period": label,
                "trades": m["total_trades"],
                "wr":     m["win_rate"],
                "pf":     m["profit_factor"],
                "dd":     m["max_drawdown_pct"],
                "pnl":    m["net_profit"],
            })
            print(f"  {symbol:<10} {label:<10}  trades={m['total_trades']:>3}  "
                  f"WR={m['win_rate']*100:>5.1f}%  PF={m['profit_factor']:>7.2f}  "
                  f"DD={m['max_drawdown_pct']:>5.1f}%  PnL=${m['net_profit']:>10,.2f}")
            sys.stdout.flush()
        except Exception as exc:
            print(f"  {symbol:<10} {label:<10}  ERROR: {exc}")
            sys.stdout.flush()

print("\n\n===== SUMMARY =====")
print(f"{'Symbol':<10} {'Period':<10} {'Trades':>6} {'WR%':>6} {'PF':>7} {'DD%':>6} {'Net PnL':>12}")
print("-" * 65)
for r in sorted(results, key=lambda x: x["pnl"], reverse=True):
    print(f"{r['symbol']:<10} {r['period']:<10} {r['trades']:>6} "
          f"{r['wr']*100:>6.1f} {r['pf']:>7.2f} {r['dd']:>6.1f} ${r['pnl']:>11,.2f}")
