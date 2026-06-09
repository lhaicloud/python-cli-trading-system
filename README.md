# LQ-MTF Strategy
**Liquidity-Quality Multi-Timeframe Trading System**

Python CLI-only crypto paper trading, backtesting, signal-generation,
and self-learning system. No UI. No web app. CLI only.

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Copy and edit config
cp .env.example .env

# 3. Backfill historical data (BTCUSDT, 2021–present)
python main.py backfill --symbol BTCUSDT --timeframes 1d 4h 1h 30m --start 2021-01-01

# 4. Run market analysis
python main.py analyze --symbol BTCUSDT

# 5. Generate a signal
python main.py signal --symbol BTCUSDT

# 6. Run a backtest
python main.py backtest --symbol BTCUSDT --start 2021-01-01 --end 2024-12-31 --capital 10000

# 7. Run paper trading cycle
python main.py paper --symbol BTCUSDT --capital 10000

# 8. Train models (needs 20+ labelled trades from paper trading)
python main.py train --symbol BTCUSDT --model both

# 9. Validate and activate a model
python main.py validate --symbol BTCUSDT --accept

# 10. View reports
python main.py report --symbol BTCUSDT
python main.py report --symbol BTCUSDT --type backtest
python main.py report --symbol BTCUSDT --type learning
```

---

## Commands

| Command     | Description |
|-------------|-------------|
| `backfill`  | Download historical OHLCV candles from Binance |
| `update`    | Fetch only missing/latest candles (incremental) |
| `analyze`   | Full multi-timeframe analysis printout |
| `signal`    | Generate BUY / SELL / HOLD signal with full breakdown |
| `backtest`  | Historical simulation (no lookahead bias) |
| `paper`     | One paper trading cycle (signal → open/update trades) |
| `train`     | Train BUY or SELL classifier from stored outcomes |
| `validate`  | Validate a candidate model, optionally activate it |
| `report`    | Performance / learning / backtest reports |

---

## Strategy Overview

**LQ-MTF** uses a 4-timeframe confluence model:

| Timeframe | Role |
|-----------|------|
| Daily     | Macro bias filter |
| 4H        | Directional bias, BOS/CHoCH, regime |
| 1H        | Setup confirmation |
| 30M       | Supply/demand zone execution, liquidity |

**Signal is only generated when:**
- 4H bias supports the direction
- Price is in a valid 30M supply/demand zone (score ≥ 65)
- 1H confirms the move
- Signal confidence ≥ 70/100
- Risk/reward ≥ 1:2
- BUY and SELL models are not both strong (conflict = HOLD)

---

## Project Structure

```
main.py                   # CLI entry point
requirements.txt
.env.example

app/
  cli.py                  # All CLI commands (typer)
  config.py               # Settings (pydantic-settings)

  data/
    binance_client.py     # Binance REST API (public, no auth)
    backfill.py           # Historical + incremental download
    repository.py         # All SQL queries
    validators.py         # Data quality checks

  db/
    connection.py         # SQLite WAL connection manager
    schema.sql            # Full schema (15 tables)
    migrations.py         # Schema init on startup

  ta/
    indicators.py         # EMA, ATR, RSI, volume
    trend.py              # EMA alignment, directional bias
    market_structure.py   # Swing detection, BOS, CHoCH
    liquidity.py          # Swing highs/lows, equal levels, sweeps
    zones.py              # Supply/demand zone detection
    zone_scoring.py       # 8-dimension zone score (0–100)
    regime.py             # Market regime classifier (8 states)
    signals.py            # BUY/SELL/HOLD signal generator

  models/
    buy_model.py          # BUY model wrapper
    sell_model.py         # SELL model wrapper
    trainer.py            # GradientBoosting trainer + CV
    validator.py          # Out-of-sample validation + activation
    versioning.py         # Model file paths, version tags

  backtesting/
    engine.py             # Candle-by-candle simulation
    metrics.py            # Full performance metrics
    simulator.py          # Paper trade open/close/update

  paper/
    account.py            # Capital state
    broker.py             # Simulated fill with slippage
    trade_manager.py      # Paper session orchestrator

  reports/
    reporter.py           # General + signal report (Rich)
    backtest_report.py    # Detailed backtest report
    learning_report.py    # Self-learning analysis report

  utils/
    logger.py             # Centralised logging
    timeframes.py         # TF → ms, dt, ordering
    math_utils.py         # ATR, EMA, RSI, position sizing
```

---

## Zone Rating System

| Score   | Rating | Action |
|---------|--------|--------|
| 85–100  | A+     | Institutional-quality — trade with full confidence |
| 75–84   | A      | High quality — trade |
| 65–74   | B      | Good — trade with caution |
| 50–64   | C      | Watch only |
| < 50    | ignore | Skip   |

---

## Signal Confidence

| Score  | Action |
|--------|--------|
| 80–100 | Strong signal |
| 70–79  | Valid signal |
| 60–69  | Paper trade only |
| < 60   | HOLD |

---

## Risk Management Defaults

- Risk per trade: **1%** of capital
- Max daily loss: **3%**
- Max open trades: **1**
- Minimum R:R: **1:2**
- Fees: 0.1% each side
- Slippage: 0.05%

---

## Self-Learning Workflow

1. `backfill` → download data
2. `backtest` → get initial performance baseline
3. `paper` → run paper trades (generates feature snapshots)
4. Trades close → outcomes labelled automatically (win/loss)
5. `train` → train models on labelled outcomes
6. `validate --accept` → validate + activate if better
7. `report --type learning` → see what improved and what to fix

---

## Database (SQLite)

Located at `data/db/lqmtf.db` (configurable via `.env`).

Tables: `symbols`, `candles`, `liquidity_levels`, `zones`, `signals`,
`paper_trades`, `model_versions`, `training_runs`, `backtest_runs`,
`backtest_trades`, `model_metrics`, `feature_snapshots`,
`learning_reports`, `data_quality_reports`, `app_settings`
