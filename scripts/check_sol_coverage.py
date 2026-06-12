import datetime
from app.db.connection import get_conn

with get_conn() as conn:
    rows = conn.execute("""
        SELECT strftime('%Y-%m', datetime(entry_time/1000, 'unixepoch')) as month,
               COUNT(*) as cnt
        FROM backtest_trades
        WHERE symbol='SOLUSDT' AND features IS NOT NULL
        GROUP BY month ORDER BY month
    """).fetchall()
    print("SOL trades by month:")
    for r in rows:
        print(f"  {r[0]}: {r[1]} trades")
    print(f"  Total: {sum(r[1] for r in rows)}")
