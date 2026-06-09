import datetime
from app.db.connection import get_conn
with get_conn() as conn:
    rows = conn.execute("""
        SELECT symbol, COUNT(*) as cnt,
               MIN(entry_time) as oldest, MAX(entry_time) as newest
        FROM backtest_trades
        WHERE features IS NOT NULL
        GROUP BY symbol ORDER BY symbol
    """).fetchall()
    for r in rows:
        oldest = datetime.datetime.fromtimestamp(r[2]/1000, tz=datetime.timezone.utc).strftime('%Y-%m-%d')
        newest = datetime.datetime.fromtimestamp(r[3]/1000, tz=datetime.timezone.utc).strftime('%Y-%m-%d')
        print(f"  {r[0]}: {r[1]:,} trades  ({oldest} to {newest})")
