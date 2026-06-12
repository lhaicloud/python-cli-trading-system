"""Check all active models and their metadata."""
import sqlite3, json
from pathlib import Path

DB = r"D:\AI Projects\python-cli-trading-system\data\db\lqmtf.db"
conn = sqlite3.connect(DB)

print("=== ACTIVE SIGNAL MODELS ===")
rows = conn.execute("""
    SELECT mv.symbol, mv.model_type, mv.algorithm, mv.file_path,
           mm.f1_score as holdout_f1, mm.win_rate as holdout_wr,
           mv.created_at
    FROM model_versions mv
    LEFT JOIN model_metrics mm ON mm.model_version_id = mv.id AND mm.metric_type = 'validation'
    WHERE mv.status = 'active'
    ORDER BY mv.symbol, mv.model_type
""").fetchall()

for r in rows:
    fname = Path(r[3]).name if r[3] else "N/A"
    exists = Path(r[3]).exists() if r[3] else False
    f1 = f"{r[4]:.3f}" if r[4] else "?"
    wr = f"{r[5]:.1%}" if r[5] else "?"
    flag = "OK" if exists else "MISSING"
    print(f"  {r[0]:12s} {r[1]:15s} [{r[2]:3s}] {f1:6s} F1 {wr:7s} wr  {flag}  {r[6][:10]}")

print("\n=== FEATURE SNAPSHOT COUNTS ===")
rows2 = conn.execute("""
    SELECT symbol, COUNT(*) as total,
           SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) as wins,
           SUM(CASE WHEN outcome='loss' THEN 1 ELSE 0 END) as losses
    FROM feature_snapshots
    WHERE outcome IS NOT NULL
    GROUP BY symbol ORDER BY symbol
""").fetchall()
for r in rows2:
    wr = r[2]/(r[2]+r[3])*100 if (r[2]+r[3]) > 0 else 0
    print(f"  {r[0]:12s} {r[1]:4d} total  {r[2]:4d}W / {r[3]:4d}L  ({wr:.1f}% wr)")

conn.close()
