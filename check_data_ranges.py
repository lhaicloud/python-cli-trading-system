import sqlite3
conn = sqlite3.connect('data/db/lqmtf.db')
cur = conn.cursor()
cur.execute("""
    SELECT symbol, timeframe,
           datetime(MIN(open_time)/1000, 'unixepoch') as earliest,
           datetime(MAX(open_time)/1000, 'unixepoch') as latest,
           COUNT(*) as candles
    FROM candles
    GROUP BY symbol, timeframe
    ORDER BY symbol, timeframe
""")
for row in cur.fetchall():
    print(f"  {row[0]:10s} {row[1]:4s}: {row[2]} to {row[3]}  ({row[4]:,} candles)")
conn.close()
