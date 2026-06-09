from app.db.connection import get_conn
with get_conn() as conn:
    row = conn.execute("SELECT sql FROM sqlite_master WHERE name='backtest_trades'").fetchone()
    print(row[0])
