"""
Add UNIQUE index on (symbol, timestamp) to feature_snapshots.
This enables INSERT OR IGNORE deduplication on future seeds.
"""
from app.db.connection import get_conn

with get_conn() as conn:
    # Check if index already exists
    existing = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='uq_feature_snapshots_sym_ts'"
    ).fetchone()

    if existing:
        print("Index already exists.")
    else:
        conn.execute("""
            CREATE UNIQUE INDEX uq_feature_snapshots_sym_ts
            ON feature_snapshots (symbol, timestamp)
        """)
        conn.commit()
        print("Created UNIQUE index on feature_snapshots(symbol, timestamp)")

    # Verify
    count = conn.execute("SELECT COUNT(*) FROM feature_snapshots").fetchone()[0]
    print(f"Current row count: {count:,} (no duplicates should exist)")
