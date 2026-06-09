"""
Full ML pipeline: seed -> dedup -> audit -> compare models -> retrain -> save report.
Run this AFTER all data expansion scripts have completed.
"""
import subprocess
import sys

PYTHON = sys.executable

def run(script, label):
    print(f"\n{'='*60}")
    print(f"  STEP: {label}")
    print(f"{'='*60}")
    result = subprocess.run([PYTHON, script], capture_output=False, timeout=600)
    if result.returncode != 0:
        print(f"  WARNING: {script} exited with code {result.returncode}")
    return result.returncode == 0

# Step 1: Seed new backtest_trades data into feature_snapshots (INSERT OR IGNORE = no dupes)
run("seed_training_data.py", "Seed new training data")

# Step 2: Verify no duplicates (safety net)
run("dedup_snapshots.py", "Dedup feature_snapshots")

# Step 3: Audit data quality
run("ml_audit.py", "Data quality audit")

# Step 4: Compare algorithms (GBM vs XGB vs RF)
run("ml_model_compare.py", "Algorithm comparison")

# Step 5: Retrain all models with tuned hyperparams
run("ml_retrain_tuned.py", "Retrain all models")

print("\n\nPipeline complete. Now run ab_test.py to measure ML edge.")
