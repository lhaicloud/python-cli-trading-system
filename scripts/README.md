# Ad-hoc scripts

One-off diagnostics, backfill helpers, per-coin backtests and ML experiments
moved out of the repo root. They import `app.*`, so run them **from the repo
root** as modules:

```bash
python -m scripts.check_db
python -m scripts.eth_backtest
```

(`python scripts/check_db.py` will fail to import `app` — the script's own
directory, not the cwd, goes on `sys.path`.)

Kept at the root because they are operational or documented in CLAUDE.md:
`main.py`, `menu.py`, `run_live_paper.py`, `run_live_rotation.py`,
`run_handoff.py`, `scan_universe.py`, `test_imports.py`, `smoke_test.py`,
`quick_backtest.py`, `ml_retrain_tuned.py`.
