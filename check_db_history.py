from dotenv import load_dotenv; load_dotenv()
from app.db.connection import get_conn
with get_conn() as conn:
    runs = conn.execute("""
        SELECT symbol, COUNT(*) as runs,
               AVG(win_rate) as avg_wr,
               SUM(net_profit) as total_pnl,
               SUM(total_trades) as total_trades
        FROM backtest_runs
        GROUP BY symbol ORDER BY total_pnl DESC
    """).fetchall()
    print(f"{'Symbol':<12} {'Runs':>4} {'Avg WR':>8} {'Total PnL':>12} {'Trades':>8}")
    print("-" * 50)
    for r in runs:
        print(f"{r[0]:<12} {r[1]:>4} {r[2]*100:>7.1f}% ${r[3]:>10,.0f} {r[4]:>8}")

    snaps = conn.execute("""
        SELECT symbol, COUNT(*) as n,
               SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) as wins
        FROM feature_snapshots
        GROUP BY symbol ORDER BY n DESC
    """).fetchall()
    print()
    print(f"{'Symbol':<12} {'Snapshots':>10} {'Wins':>6}")
    print("-" * 32)
    for r in snaps:
        print(f"{r[0]:<12} {r[1]:>10} {r[2]:>6}")
