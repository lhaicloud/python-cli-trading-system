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
python main.py live --symbol BTCUSDT

# Ad-hoc test scripts (no pytest)
python test_imports.py
python smoke_test.py
python quick_backtest.py
python ml_retrain_tuned.py   # preferred ML retraining script
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
7. R:R validation (minimum 1:2 required)

**Backtesting Engine** (`app/backtesting/engine.py`):
- Pre-computes all indicators once before the candle loop (no lookahead bias)
- 30m candles as primary timeframe; loads 1d/12h/4h/1h/30m/1w datasets
- Simulated fills: 0.1% fee + 0.05% slippage
- Ratchet stop-loss + trailing logic

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
