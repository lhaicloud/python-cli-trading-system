import sqlite3

conn = sqlite3.connect('data/db/lqmtf.db')
cur = conn.cursor()

cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in cur.fetchall()]
print("Tables:", tables)

for t in ['feature_snapshots', 'backtest_trades', 'model_versions', 'training_runs']:
    if t in tables:
        cur.execute(f"SELECT COUNT(*) FROM {t}")
        cnt = cur.fetchone()[0]
        print(f"\n{t}: {cnt} rows")
        if cnt > 0:
            cur.execute(f"SELECT * FROM {t} LIMIT 1")
            cols = [d[0] for d in cur.description]
            row = dict(zip(cols, cur.fetchone()))
            print(f"  cols: {list(row.keys())}")
            if 'outcome' in row:
                cur.execute(f"SELECT outcome, COUNT(*) FROM {t} GROUP BY outcome")
                for r in cur.fetchall():
                    print(f"  outcome={r[0]}: {r[1]}")
            if 'features' in row:
                print(f"  sample features: {str(row['features'])[:300]}")
    else:
        print(f"\n{t}: NOT FOUND")

conn.close()
