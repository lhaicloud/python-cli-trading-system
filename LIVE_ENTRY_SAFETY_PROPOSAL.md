# Live entry safety — fail-closed Owner / Risk / LIVE_HALT gates

**Status:** implemented in code. This document is the canonical semantics.
**This is not live-trading approval.** HALT NEW TRADING remains policy.
`--yes` and `--live-execution` never authorize an order.

## Problem

`python main.py live --live-execution --yes` can place real Binance USDT-M
futures orders:

- `--yes` only skips `typer.confirm` (needed for nohup/systemd).
- `LiveExecutor.open_trade` had no Owner / Risk gate before `place_*`.
- `BINANCE_TESTNET` defaults to **false** (mainnet) in `app/config.py`.
- Deploy unit `deploy/lqmtf-live.service` passes `--live-execution --yes`.

Paper trading (`python main.py live` without `--live-execution`,
`app/live/watcher.py`) is unchanged.

## Gates (canonical names)

| Name | Meaning |
|------|---------|
| `LIVE_HALT` | If true ⇒ deny **all** new live entries (`deny_code=live_halt`). False or absent does not grant access; Owner + Risk are still required. |
| `OWNER_AUTHORIZATION` | Required bool (`true`/`1`/`yes`/`on`) or opaque token. Missing/false ⇒ `missing_owner_auth`. |
| `RISK_APPROVED` | Required bool or token. Missing/false ⇒ `missing_risk_approved`. When the value is a **token** (not a bool), it must equal `trade_intent_id` or the deny code is `intent_mismatch`. |
| `trade_intent_id` / `TRADE_INTENT_ID` | Per-trade binding for a Risk token. Optional when Risk is a bool. |
| `OWNER_AUTHORIZATION_EXPIRES` / `RISK_APPROVED_EXPIRES` | Optional unix timestamp or ISO-8601. Past or malformed ⇒ `expired`. |
| `--yes` / `yes_flag` | **Never** satisfies Owner or Risk. |
| `--live-execution` | **Never** satisfies Owner or Risk. |

Unrecognised non-empty `LIVE_HALT` values fail closed (treated as halt).

## Deny-code precedence

First match wins:

1. `live_halt` — `LIVE_HALT` is true
2. `expired` — Owner credential present but expiry is past/malformed
3. `yes_insufficient` — Owner missing/false **and** `yes_flag=True`
4. `missing_owner_auth` — Owner missing/false and `yes_flag=False`
5. `expired` — Risk credential present but expiry is past/malformed
6. `missing_risk_approved` — Owner valid and Risk missing/false
7. `intent_mismatch` — Risk is a per-trade token that does not match `trade_intent_id`

Required case: Owner true, Risk false, `yes_flag=True` ⇒ **`missing_risk_approved`**
(not `yes_insufficient`). `yes_insufficient` is only when Owner is missing/false
and `yes_flag` is set.

Allow only when: `LIVE_HALT` is false/absent **and** Owner valid **and** Risk valid
**and** (if Risk is a token) intent matches **and** credentials are not expired.

## Enforcement points

1. **CLI** (`app/cli.py` `live --live-execution`, not `dry_run`): pre-check
   before constructing the Binance client / starting `LiveTradingWatcher`.
   Failed check prints `deny_code` and `typer.Exit(1)`. Paper branch is not
   entered.
2. **LiveTradingWatcher.__init__**: if not `dry_run`, raise `LiveEntryDenied`
   **before** constructing `BinanceExchangeClient`.
3. **LiveExecutor.open_trade**: hard stop **before any `place_*` order**
   (and before equity/mark-price/leverage calls). Returns `("blocked", None)`.
4. **LiveExecutor.handle_entry_limit_fill**: do not *add* a `live_trades` row
   when gates now deny; flatten the irreversible fill (`reduce_only` close)
   using the existing stale-guard path.

`app/live/live_entry_auth.py` is the single decision function. Tests pass an
explicit `environ={}` mapping so they never read `.env`.

## What this change does **not** do

- Does not set `OWNER_AUTHORIZATION` or `RISK_APPROVED` true anywhere.
- Does not change `BINANCE_TESTNET` default (still false / mainnet).
- Does not enable or start systemd live units.
- Does not bake authorization into `deploy/lqmtf-live.service`.
  Comments there document optional `LIVE_HALT=true` and that `--yes` is not
  approval. Removing `--yes` from a headless unit is optional; the auth
  module still denies without Owner/Risk.

## Tests

```bash
python -m scripts.test_live_entry_auth
```

Covers: default deny, `--yes` alone, owner-only, both allow, `LIVE_HALT`,
owner+yes without risk → `missing_risk_approved`, and `open_trade` placing
zero orders when denied.
