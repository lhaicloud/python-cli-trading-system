"""Quick test of zone buffer fix on key scenarios."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from dotenv import load_dotenv; load_dotenv()
from app.db.migrations import run_migrations; run_migrations()
from app.backtesting.engine import run_backtest
from app.utils.timeframes import str_to_ms

scenarios = [
    # The failing BNB bear scenario
    ("BNB BEAR Q2-2022",   "BNBUSDT", "2022-04-01", "2022-06-30"),
    # BTC bear (was working — check no regression)
    ("BTC BEAR Q2-2022",   "BTCUSDT", "2022-04-01", "2022-06-30"),
    # BTC bull (most important reference — check no regression)
    ("BTC BULL Q1-2023",   "BTCUSDT", "2023-01-01", "2023-03-31"),
    # SOL bear (was working)
    ("SOL BEAR Q2-2022",   "SOLUSDT", "2022-04-01", "2022-06-30"),
    # ETH bear (was working)
    ("ETH BEAR Q2-2022",   "ETHUSDT", "2022-04-01", "2022-06-30"),
]

print("=== Zone Buffer Fix Validation ===")
print("Before: buffer = zone_width * pct (zone-relative, ~0.05% of price)")
print("After:  buffer = price    * pct (price-relative, exactly 2% of price)")
print()

results = []
for label, sym, start, end in scenarios:
    r = run_backtest(sym, str_to_ms(start), str_to_ms(end), 10_000.0, 1.0)
    m = r["metrics"]
    t = r["trades"]
    net = m["net_profit"]
    wr  = m["win_rate"]
    pf  = m.get("profit_factor", 0)
    tc  = m["total_trades"]
    sign = "+" if net >= 0 else ""
    print(f"  {label:28s}: {tc:3d}T  {wr:.0%} wr  PF={pf:.2f}  {sign}${net:,.0f}")
    results.append((label, tc, net))

print()
total = sum(r[2] for r in results)
profit_count = sum(1 for r in results if r[2] > 0)
print(f"  Total: {sign}${total:,.0f}  ({profit_count}/{len(results)} profitable)")
