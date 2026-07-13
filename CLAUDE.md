# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Setup
pip install -r requirements.txt
cp .env.example .env

# Core CLI commands
python main.py backfill --symbol BTCUSDT --timeframes 1d 4h 1h 30m --start 2021-01-01
python main.py update --symbol BTCUSDT --timeframes 1d 4h 1h 30m
python main.py analyze --symbol BTCUSDT
python main.py signal --symbol BTCUSDT
python main.py backtest --symbol BTCUSDT --start 2023-01-01 --end 2024-01-01
python main.py paper --symbol BTCUSDT
python main.py train --symbol BTCUSDT
python main.py validate --symbol BTCUSDT
python main.py report --type general
python main.py analyze-trades   # full paper-trade analysis + capital reconciliation
python main.py live --symbol BTCUSDT

# Ad-hoc test scripts (no pytest)
python test_imports.py
python smoke_test.py
python quick_backtest.py
python ml_retrain_tuned.py   # preferred ML retraining script

# One-off diagnostics/experiments live in scripts/ — run as modules from the root:
python -m scripts.check_db
python -m scripts.verify_phase1_fixes    # needs DB_PATH pointed at a throwaway DB
python -m scripts.verify_phase5          # needs DB_PATH pointed at a throwaway DB
python -m scripts.repair_paper_trades    # data repair, dry-run by default
```

To disable ML model during signal generation: `DISABLE_ML_MODEL=1 python main.py signal --symbol BTCUSDT`

## Architecture

**Entry:** `main.py` → `app/cli.py` (Typer CLI, 10 commands) → submodules

**Database:** Single SQLite file (`data/db/lqmtf.db`, WAL mode), 15 tables. Access via `with get_conn() as conn:` context manager (`app/db/connection.py`). Schema defined in `app/db/schema.sql`.

**Configuration:** Pydantic Settings (`app/config.py`) reads `.env` file. All modules import the singleton `settings` object. Key env vars: `DB_PATH`, `MODEL_DIR`, `BINANCE_BASE_URL`, `DISABLE_ML_MODEL`, plus risk params and MTF strategy weights.

**Signal Generation Pipeline** (`app/ta/signals.py` → `SignalResult` dataclass):
1. Trend bias from 4 timeframes (1d/4h/1h/30m) → directional score
2. Market regime classification (8 states, `app/ta/regime.py`)
3. Liquidity level detection + sweep detection (`app/ta/liquidity.py`)
4. Supply/demand zone detection + 8-dimension scoring 0–100 (`app/ta/zones.py`, `app/ta/zone_scoring.py`)
5. Optional ML confidence from separate BUY/SELL classifiers
6. Multi-timeframe confluence scoring (`app/ta/mtf_scorer.py`)
7. R:R validation (minimum = `min_rr_ratio` in config, default 1.8)

**Backtesting Engine** (`app/backtesting/engine.py`):
- Pre-computes all indicators once before the candle loop (no lookahead bias)
- 30m candles as primary timeframe; loads 1d/12h/4h/1h/30m/1w datasets
- Simulated fills: fee + slippage on entry AND on stop-type exits; TP exits fill at limit
- Same-candle SL+TP resolves via candle-open proximity, ties to SL (conservative)
- Applies the live `SignalFilter` pipeline (live/backtest parity); runs tagged `rule_based_v2_realistic`
- Ratchet stop-loss + trailing logic (trails off the *previous* candle's extremes)

**Risk & Execution Model** (`app/paper/`, Phase-5 upgrades):
- **Unified portfolio equity** (`paper_capital_portfolio` setting) — sizing and all
  risk caps read one equity pool; per-symbol `paper_capital_<sym>` ledgers remain
  for reporting. Bootstrapped as `default_capital + sum(closed PnL)`.
- **Risk budget**: total open `capital_at_risk` capped at `MAX_OPEN_RISK_PCT` (3%);
  same-direction positions risk-scaled 1.0 / 0.7 / 0.5 (`corr_risk_scale`).
- **Entry limit orders** (`ENTRY_LIMIT_ENABLED`, **default off**): when price must
  retrace to the signal entry, a simulated limit rests in `pending_orders` and
  fills via `PositionManager.check_pending()`; expires after
  `ENTRY_LIMIT_EXPIRY_CANDLES` × 30m. Kept off by default — BTC Q1-2025 backtest
  showed it skips the best trades (winners enter and never retrace).
  Live execution has an **independent** flag, `LIVE_ENTRY_LIMIT_ENABLED` (also
  default off, untested against real fills), reusing the same
  `ENTRY_LIMIT_EXPIRY_CANDLES` / `ENTRY_LIMIT_MIN_GAP_PCT` knobs — it places a
  real resting `LIMIT` order on Binance (`LiveExecutor.open_trade()` →
  `place_limit_order`) rather than a simulated one, tracked in
  `live_pending_entries` and finalized asynchronously via
  `handle_entry_limit_fill()` when `UserDataStream` reports the fill. Because a
  real fill can't be undone the way a simulated one can, guards are re-checked
  at fill time; if they now fail, the position is immediately market-flattened
  instead of finalized. Meant to test whether resting live entries reduce the
  real-time-drift rejections seen in `open_trade`'s market-price R:R re-check —
  a different failure mode than the backtest finding above.
- **Partial TP**: 50% closed at +1.5R (`PARTIAL_TP_*`); final `pnl` column folds
  partial + remainder; capital is updated incrementally (use `pnl_increment`
  from `PositionManager.close()`, never `pnl`, for capital updates).
- **Live safety gates**: daily loss breaker (3% of equity, realized, UTC day) and
  48h stagnation timeout (`MAX_TRADE_AGE_HOURS`) — parity with backtest engine.
- **SignalFilter extras**: macro-event guard (`data/macro_events.json`, ±2h FOMC/CPI)
  and funding-rate filter (blocks crowded-side entries; live uses the API,
  backtests read the `funding_rates` table — populate via
  `python -m scripts.backfill_funding`).
- **PriceMonitor**: Binance futures WebSocket (miniTicker) primary with silence
  watchdog; falls back to 60s REST polling automatically.

**Rotation** (`run_live_rotation.py` + `scan_universe.py`):
- Conviction-based: `select_watchlist(n=5, validated_only=True, pool=coin_pool.json)`
  ranks the curated pool by MTF gap / regime / zone proximity; validated coins
  first, padded with the best unvalidated pool coins. Random pick is only a
  fallback when the scanner errors.
- ONE multi-symbol `LiveWatcher` (shared PositionManager/PriceMonitor/Telegram
  listener) with `rescan_every=48` — daily re-pick happens IN-PROCESS (no
  restart needed); open trades are pinned, dangerous regimes emergency-close.
- "Validated" = ≥1 profitable backtest run from the CURRENT engine version
  (`ENGINE_VERSION` in `app/backtesting/engine.py`); old `rule_based_v1` runs
  don't count. Re-baseline with `python -m scripts.revalidate_pool --run`.
- `rotation_history.json` is audit-only (the avoid-yesterday rule was removed —
  trend persistence is the edge). Runner writes `live_pids.json` for
  `run_handoff.py`.
- Position sizing caps notional at `capital × leverage`
  (`app/utils/math_utils.position_size`).

**ML Pipeline** (`app/models/`):
- 15 feature columns: bias scores, zone score, liquidity sweep, RSI, volume ratio, ATR expansion, etc.
- Separate `GradientBoostingClassifier` for BUY and SELL signals
- `StratifiedKFold` + `TimeSeriesSplit` cross-validation
- Models saved as joblib dumps with timestamp-based version tags (`app/models/versioning.py`)
- Use `ml_retrain_tuned.py` for retraining (fixes dedup, dead features, CV leakage)

**Module Map:**

| Module | Responsibility |
|--------|---------------|
| `app/cli.py` | All 10 CLI commands |
| `app/config.py` | Pydantic settings + `.env` loading |
| `app/data/` | Binance REST client, backfill, DB repository, validators |
| `app/db/` | SQLite connection, schema, migrations |
| `app/ta/` | All TA: indicators, trend, market structure, liquidity, zones, scoring, signals, regime, MTF |
| `app/models/` | ML trainer, validator, buy/sell models, versioning |
| `app/backtesting/` | Backtest engine, simulator, metrics |
| `app/paper/` | Paper trading: trade manager, account, broker |
| `app/reports/` | Rich-formatted output for all report types |
| `app/live/` | Auto signal + paper trading watcher |
| `app/notifications/` | Optional Telegram alerts |
| `app/utils/` | Logger, timeframe helpers, math utils |

## Key Conventions

- **No async** — Binance API calls are synchronous via `httpx`
- **Rich output** — all user-facing output uses `rich` (tables, progress, panels); use `console.print()` not `print()`
- **Dataclass results** — core outputs are typed dataclasses (`SignalResult`, `OpenTrade`), not dicts
- **No test framework** — tests are standalone Python scripts, not pytest
- **Timeframes** — canonical set is `["1d", "12h", "4h", "1h", "30m", "1w"]`; `app/utils/timeframes.py` has helpers
- **Windows dev** — use `venv-win\Scripts\python.exe` (the `venv/` dir is a Linux venv from the live host); point `DB_PATH` at a copy (e.g. `data/db/lqmtf_dev.db`) before running anything that writes — `data/db/lqmtf.db` is synced with the live runner
- **Telegram** — only one process/thread may long-poll getUpdates per token; `TELEGRAM_LISTENER=0` (env) or `LiveWatcher(telegram_listener=False)` disables the listener
