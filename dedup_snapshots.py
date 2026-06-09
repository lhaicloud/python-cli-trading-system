"""
Deduplicates feature_snapshots by (symbol, timestamp).
Keeps the first (lowest rowid) occurrence of each pair.
"""
from app.db.connection import get_conn

with get_conn() as conn:
    before = conn.execute("SELECT COUNT(*) FROM feature_snapshots").fetchone()[0]

    dup_rows = conn.execute("""
        SELECT COUNT(*) FROM feature_snapshots
        WHERE rowid NOT IN (
            SELECT MIN(rowid) FROM feature_snapshots
            GROUP BY symbol, timestamp
        )
    """).fetchone()[0]

    print(f"Total rows before: {before:,}")
    print(f"Duplicate rows to remove: {dup_rows:,}")

    conn.execute("""
        DELETE FROM feature_snapshots
        WHERE rowid NOT IN (
            SELECT MIN(rowid) FROM feature_snapshots
            GROUP BY symbol, timestamp
        )
    """)
    conn.commit()

    after = conn.execute("SELECT COUNT(*) FROM feature_snapshots").fetchone()[0]
    print(f"Total rows after:  {after:,}")
    print(f"Removed: {before - after:,} rows")

print("Deduplication complete.")
