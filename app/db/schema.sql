-- LQ-MTF Strategy — SQLite Schema
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ─── Symbols ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS symbols (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT    UNIQUE NOT NULL,
    base_asset  TEXT,
    quote_asset TEXT,
    status      TEXT    DEFAULT 'TRADING',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ─── Candles ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS candles (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol                   TEXT    NOT NULL,
    timeframe                TEXT    NOT NULL,
    open_time                INTEGER NOT NULL,
    open                     REAL    NOT NULL,
    high                     REAL    NOT NULL,
    low                      REAL    NOT NULL,
    close                    REAL    NOT NULL,
    volume                   REAL    NOT NULL,
    close_time               INTEGER NOT NULL,
    quote_volume             REAL,
    trades                   INTEGER,
    taker_buy_base_volume    REAL,
    taker_buy_quote_volume   REAL,
    created_at               TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(symbol, timeframe, open_time)
);

CREATE INDEX IF NOT EXISTS idx_candles_symbol_tf_time
    ON candles(symbol, timeframe, open_time DESC);

-- ─── Liquidity Levels ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS liquidity_levels (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT    NOT NULL,
    timeframe   TEXT    NOT NULL,
    level_type  TEXT    NOT NULL,  -- swing_high|swing_low|equal_high|equal_low|range_high|range_low|pdh|pdl|pwh|pwl
    price       REAL    NOT NULL,
    open_time   INTEGER NOT NULL,
    swept       INTEGER DEFAULT 0,
    swept_at    INTEGER,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_liq_symbol_tf
    ON liquidity_levels(symbol, timeframe);

-- ─── Supply / Demand Zones ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS zones (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol                   TEXT    NOT NULL,
    timeframe                TEXT    NOT NULL,
    zone_type                TEXT    NOT NULL,  -- demand|supply
    zone_top                 REAL    NOT NULL,
    zone_bottom              REAL    NOT NULL,
    displacement_candle_time INTEGER NOT NULL,
    score                    REAL    DEFAULT 0,
    rating                   TEXT,              -- A+|A|B|C|ignore
    status                   TEXT    DEFAULT 'active',  -- active|mitigated|broken
    touch_count              INTEGER DEFAULT 0,
    score_breakdown          TEXT,              -- JSON
    created_at               TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at               TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_zones_symbol_tf_status
    ON zones(symbol, timeframe, status);

-- ─── Signals ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS signals (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol              TEXT    NOT NULL,
    signal              TEXT    NOT NULL,  -- BUY|SELL|HOLD
    confidence          REAL,
    market_regime       TEXT,
    daily_bias          TEXT,
    h4_bias             TEXT,
    h1_confirmation     TEXT,
    m30_zone_type       TEXT,
    zone_id             INTEGER REFERENCES zones(id),
    zone_score          REAL,
    zone_rating         TEXT,
    liquidity_sweep     INTEGER DEFAULT 0,
    premium_discount    TEXT,
    entry_price         REAL,
    stop_loss           REAL,
    take_profit         REAL,
    risk_reward         REAL,
    setup_type          TEXT,
    reasons             TEXT,  -- JSON array
    warnings            TEXT,  -- JSON array
    rejection_reason    TEXT,  -- human-readable HOLD/BLOCKED reason
    model_version       TEXT,
    data_quality        TEXT,
    timestamp           INTEGER NOT NULL,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_signals_symbol_time
    ON signals(symbol, timestamp DESC);

-- ─── Paper Trades ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS paper_trades (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol                   TEXT    NOT NULL,
    signal_id                INTEGER REFERENCES signals(id),
    direction                TEXT    NOT NULL,  -- BUY|SELL
    status                   TEXT    DEFAULT 'open',  -- open|closed|stopped|target_hit
    entry_price              REAL    NOT NULL,
    stop_loss                REAL    NOT NULL,
    take_profit              REAL    NOT NULL,
    position_size            REAL    NOT NULL,
    capital_at_risk          REAL,
    risk_reward              REAL,
    open_time                INTEGER NOT NULL,
    close_time               INTEGER,
    close_price              REAL,
    pnl                      REAL    DEFAULT 0,
    pnl_pct                  REAL    DEFAULT 0,
    max_favorable_excursion  REAL    DEFAULT 0,
    max_adverse_excursion    REAL    DEFAULT 0,
    model_version            TEXT,
    created_at               TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at               TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ─── Model Versions ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS model_versions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol           TEXT    NOT NULL,
    model_type       TEXT    NOT NULL,  -- buy|sell
    version          TEXT    NOT NULL,
    algorithm        TEXT,
    features         TEXT,  -- JSON array
    hyperparameters  TEXT,  -- JSON object
    file_path        TEXT,
    status           TEXT    DEFAULT 'candidate',  -- candidate|active|rejected|archived
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(symbol, model_type, version)
);

-- ─── Training Runs ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS training_runs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol           TEXT    NOT NULL,
    model_type       TEXT    NOT NULL,
    model_version_id INTEGER REFERENCES model_versions(id),
    train_start      INTEGER,
    train_end        INTEGER,
    samples          INTEGER,
    features         TEXT,  -- JSON
    metrics          TEXT,  -- JSON
    status           TEXT    DEFAULT 'completed',
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ─── Backtest Runs ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS backtest_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT    NOT NULL,
    start_time      INTEGER NOT NULL,
    end_time        INTEGER NOT NULL,
    initial_capital REAL    NOT NULL,
    final_capital   REAL,
    total_trades    INTEGER DEFAULT 0,
    win_rate        REAL,
    profit_factor   REAL,
    max_drawdown    REAL,
    net_profit      REAL,
    metrics         TEXT,  -- JSON
    model_version   TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ─── Backtest Trades ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS backtest_trades (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    backtest_run_id          INTEGER NOT NULL REFERENCES backtest_runs(id),
    symbol                   TEXT    NOT NULL,
    direction                TEXT    NOT NULL,  -- BUY|SELL
    entry_price              REAL    NOT NULL,
    exit_price               REAL,
    stop_loss                REAL    NOT NULL,
    take_profit              REAL    NOT NULL,
    position_size            REAL    NOT NULL,
    pnl                      REAL,
    pnl_pct                  REAL,
    entry_time               INTEGER,
    exit_time                INTEGER,
    exit_reason              TEXT,  -- sl_hit|tp_hit|end_of_data
    max_favorable_excursion  REAL,
    max_adverse_excursion    REAL,
    signal_confidence        REAL,
    zone_score               REAL,
    setup_type               TEXT,
    features                 TEXT   -- JSON
);

CREATE INDEX IF NOT EXISTS idx_bt_trades_run
    ON backtest_trades(backtest_run_id);

-- ─── Model Metrics ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS model_metrics (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    model_version_id INTEGER NOT NULL REFERENCES model_versions(id),
    metric_type      TEXT    NOT NULL,  -- train|validation|test
    accuracy         REAL,
    precision_score  REAL,
    recall           REAL,
    f1_score         REAL,
    win_rate         REAL,
    profit_factor    REAL,
    max_drawdown     REAL,
    metrics          TEXT,  -- JSON
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ─── Feature Snapshots ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS feature_snapshots (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id  INTEGER REFERENCES signals(id),
    symbol     TEXT    NOT NULL,
    timestamp  INTEGER NOT NULL,
    features   TEXT    NOT NULL,  -- JSON
    outcome    TEXT,              -- win|loss|hold
    pnl        REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ─── Learning Reports ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS learning_reports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT    NOT NULL,
    report_type TEXT    NOT NULL,
    content     TEXT    NOT NULL,  -- JSON
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ─── Data Quality Reports ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS data_quality_reports (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol           TEXT    NOT NULL,
    timeframe        TEXT    NOT NULL,
    start_time       INTEGER,
    end_time         INTEGER,
    expected_candles INTEGER,
    actual_candles   INTEGER,
    missing_candles  INTEGER,
    quality_score    REAL,
    issues           TEXT,  -- JSON
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ─── App Settings ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS app_settings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    key         TEXT    UNIQUE NOT NULL,
    value       TEXT    NOT NULL,
    description TEXT,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Seed defaults
INSERT OR IGNORE INTO app_settings(key, value, description) VALUES
    ('active_buy_model_btcusdt',  'none', 'Active BUY model version for BTCUSDT'),
    ('active_sell_model_btcusdt', 'none', 'Active SELL model version for BTCUSDT'),
    ('paper_capital_btcusdt',     '10000', 'Current paper trading capital for BTCUSDT');
