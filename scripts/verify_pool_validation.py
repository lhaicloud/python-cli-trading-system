"""Verify pool validation status on server."""
import json
import sqlite3

from app.config import get_settings
from app.validation import passes_backtest_validation
from app.version import ENGINE_VERSION

DB = "/root/lqmtf/data/db/lqmtf.db"
cfg = get_settings()

with open("coin_pool.json", encoding="utf-8-sig") as f:
    pool = json.load(f)["pool"]

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
c = conn.cursor()

print("ENGINE", ENGINE_VERSION)
print("thresholds: wr>=%.0f%% trades>=%d profit>=$%.0f" % (
    cfg.backtest_min_win_rate * 100,
    cfg.backtest_min_trades,
    cfg.backtest_min_profit,
))
print("excluded", sorted(cfg.excluded_symbol_set))
print("pad_unvalidated", cfg.pool_pad_unvalidated)
print()

print(f"{'Symbol':12} {'Validated':>9} {'WR':>6} {'Trades':>6} {'Net':>8} {'PF':>6}")
print("-" * 55)
validated = []
for sym in pool:
    c.execute(
        """
        SELECT win_rate, total_trades, net_profit, profit_factor
        FROM backtest_runs
        WHERE symbol=? AND model_version=?
        ORDER BY created_at DESC LIMIT 1
        """,
        (sym, ENGINE_VERSION),
    )
    r = c.fetchone()
    if not r:
        print(f"{sym:12} {'NO BT':>9}")
        continue
    wr = r["win_rate"] or 0
    n = r["total_trades"] or 0
    net = r["net_profit"] or 0
    pf = r["profit_factor"] or 0
    ok, tier = passes_backtest_validation(
        cfg, net_profit=net, win_rate=wr, total_trades=n, profit_factor=pf,
    )
    if ok:
        validated.append(sym)
    print(
        f"{sym:12} {'YES' if ok else 'no':>9} "
        f"{wr*100:5.1f}% {n:6d} {net:8.0f} {pf:5.2f}"
        + (f"  ({tier})" if ok else "")
    )

print()
print("validated", validated, f"({len(validated)}/{len(pool)})")
conn.close()

