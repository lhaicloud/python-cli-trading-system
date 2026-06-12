"""
ML Trainer Audit — runs before any retraining decision.
Checks: duplicate snapshots, feature quality, class balance, temporal coverage.
"""
import json
import datetime
from app.db.connection import get_conn

with get_conn() as conn:
    # ── Snapshot counts ────────────────────────────────────────────────────
    total = conn.execute("SELECT COUNT(*) FROM feature_snapshots").fetchone()[0]
    dups  = conn.execute("""
        SELECT COUNT(*) FROM (
            SELECT symbol, timestamp, COUNT(*) as c
            FROM feature_snapshots
            GROUP BY symbol, timestamp
            HAVING c > 1
        )
    """).fetchone()[0]

    print(f"\n  SNAPSHOT QUALITY")
    print(f"  Total snapshots : {total:,}")
    print(f"  Duplicate (sym+ts) groups : {dups:,}")

    # ── Per-symbol breakdown ───────────────────────────────────────────────
    print(f"\n  PER-SYMBOL BREAKDOWN")
    rows = conn.execute("""
        SELECT symbol, outcome, COUNT(*) as cnt
        FROM feature_snapshots
        GROUP BY symbol, outcome
        ORDER BY symbol, outcome
    """).fetchall()
    from collections import defaultdict
    by_sym = defaultdict(dict)
    for sym, outcome, cnt in rows:
        by_sym[sym][outcome or 'null'] = cnt
    for sym, counts in sorted(by_sym.items()):
        wins   = counts.get('win', 0)
        losses = counts.get('loss', 0)
        total_sym = wins + losses
        wr = wins / total_sym if total_sym > 0 else 0
        print(f"  {sym}: {total_sym:,} total  {wins}W/{losses}L  ({wr:.1%} win rate)")

    # ── Feature completeness ───────────────────────────────────────────────
    print(f"\n  FEATURE COMPLETENESS (sample of 500 BTC snapshots)")
    sample = conn.execute("""
        SELECT features FROM feature_snapshots
        WHERE symbol='BTCUSDT' AND features IS NOT NULL
        ORDER BY RANDOM() LIMIT 500
    """).fetchall()

    from collections import Counter
    missing_counts = Counter()
    feature_keys = set()
    for (f,) in sample:
        try:
            d = json.loads(f)
            feature_keys.update(d.keys())
            for k, v in d.items():
                if v is None or v == '':
                    missing_counts[k] += 1
        except Exception:
            missing_counts['__bad_json__'] += 1

    print(f"  Feature keys found: {sorted(feature_keys)}")
    if missing_counts:
        print(f"  Missing/null values:")
        for k, v in sorted(missing_counts.items(), key=lambda x: -x[1]):
            print(f"    {k}: {v}/500 ({v/5:.1f}%)")
    else:
        print(f"  No missing values in 500-sample check")

    # ── Temporal coverage ──────────────────────────────────────────────────
    print(f"\n  TEMPORAL COVERAGE")
    rows = conn.execute("""
        SELECT symbol,
               strftime('%Y', datetime(timestamp/1000, 'unixepoch')) as yr,
               COUNT(*) as cnt
        FROM feature_snapshots
        GROUP BY symbol, yr
        ORDER BY symbol, yr
    """).fetchall()
    cur_sym = None
    for sym, yr, cnt in rows:
        if sym != cur_sym:
            if cur_sym:
                print()
            print(f"  {sym}: ", end='')
            cur_sym = sym
        print(f"{yr}:{cnt} ", end='')
    print()

print("\nAudit complete.")
