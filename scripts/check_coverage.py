from app.db.connection import get_conn
from collections import defaultdict

with get_conn() as conn:
    rows = conn.execute("""
        SELECT symbol,
               strftime('%Y-%m', datetime(entry_time/1000, 'unixepoch')) as ym,
               COUNT(*) as cnt,
               SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) as wins
        FROM backtest_trades
        GROUP BY symbol, ym
        ORDER BY symbol, ym
    """).fetchall()

by_sym = defaultdict(list)
for sym, ym, cnt, wins in rows:
    by_sym[sym].append((ym, cnt, wins))

for sym, months in by_sym.items():
    print(f"\n{sym}:")
    for ym, cnt, wins in months:
        wr = wins/cnt if cnt else 0
        bar = "+" * wins + "-" * (cnt - wins)
        print(f"  {ym}: {cnt:3d} trades  {wr:.0%} WR")
