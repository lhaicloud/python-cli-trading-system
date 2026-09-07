# AI changelog

## 2026-09-07 — Live entry fail-closed gates + forward-only model path

Safety/correctness only. **Not live approval.** HALT NEW TRADING remains
policy. Paper trading path unchanged. No `.env` secrets committed.
`OWNER_AUTHORIZATION` / `RISK_APPROVED` are **not** set true in systemd.

- Live entries now require `LIVE_HALT` false/absent **and** valid
  `OWNER_AUTHORIZATION` **and** valid `RISK_APPROVED`. `--yes` and
  `--live-execution` never satisfy those gates. Decision logic:
  `app/live/live_entry_auth.py`. Hard stop in `LiveExecutor.open_trade`
  before any `place_*`. CLI pre-check before starting the live watcher.
  Deny codes: `live_halt`, `missing_owner_auth`, `missing_risk_approved`,
  `intent_mismatch`, `yes_insufficient`, `expired`.
- ML save path no longer double-joins `model_dir` (`data/models/models/...`).
  `MODEL_PATH_FALLBACK_NESTED` defaults **false** so currently missing
  active files stay inert (score 0.0). Do not bulk-relocate/re-register.
- Docs: `LIVE_ENTRY_SAFETY_PROPOSAL.md`, `MODEL_PATH_FIX_PROPOSAL.md`.
- Tests: `python -m scripts.test_live_entry_auth`,
  `python -m scripts.test_model_path`.
