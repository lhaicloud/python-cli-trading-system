from app.db.connection import get_conn
with get_conn() as conn:
    r = conn.execute("SELECT sql FROM sqlite_master WHERE name='feature_snapshots'").fetchone()
    print(r[0])
    idx = conn.execute("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='feature_snapshots'").fetchall()
    for i in idx:
        print(i[0])
