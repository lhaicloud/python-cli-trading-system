"""Simple forward-only migration runner."""

from __future__ import annotations

from app.db.connection import get_conn, init_db
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _safe_alter(conn, sql: str) -> None:
    """Run an ALTER TABLE statement, ignoring errors if the column already exists."""
    try:
        conn.execute(sql)
        conn.commit()
    except Exception:
        pass


def run_migrations() -> None:
    """Ensure schema is up to date."""
    init_db()
    with get_conn() as conn:
        # Ratchet stop support on paper_trades
        _safe_alter(conn, "ALTER TABLE paper_trades ADD COLUMN ratchet_level INTEGER DEFAULT 0")
        _safe_alter(conn, "ALTER TABLE paper_trades ADD COLUMN original_risk REAL DEFAULT 0")
        # Leverage tracking
        _safe_alter(conn, "ALTER TABLE paper_trades ADD COLUMN leverage INTEGER DEFAULT 1")
        # HOLD/BLOCKED rejection reason stored directly (previously only in warnings JSON)
        _safe_alter(conn, "ALTER TABLE signals ADD COLUMN rejection_reason TEXT")
        # Partial take-profit: realized partial PnL is folded into the final
        # `pnl` at close; partial_pnl keeps the split visible.
        _safe_alter(conn, "ALTER TABLE paper_trades ADD COLUMN partial_taken INTEGER DEFAULT 0")
        _safe_alter(conn, "ALTER TABLE paper_trades ADD COLUMN partial_pnl REAL DEFAULT 0")
        # Historical funding rates — lets the funding filter run in backtests
        conn.execute("""
            CREATE TABLE IF NOT EXISTS funding_rates (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol       TEXT    NOT NULL,
                funding_time INTEGER NOT NULL,
                rate         REAL    NOT NULL,
                UNIQUE(symbol, funding_time)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_funding_symbol_time
                ON funding_rates(symbol, funding_time DESC)
        """)
        # Simulated resting limit orders (entry refinement)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_orders (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol          TEXT    NOT NULL,
                signal_id       INTEGER,
                direction       TEXT    NOT NULL,
                limit_price     REAL    NOT NULL,
                stop_loss       REAL    NOT NULL,
                take_profit     REAL    NOT NULL,
                risk_reward     REAL,
                model_version   TEXT,
                created_ms      INTEGER NOT NULL,
                expiry_ms       INTEGER NOT NULL,
                status          TEXT    DEFAULT 'pending',  -- pending|filled|expired|cancelled
                filled_trade_id INTEGER,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Real resting limit orders on the exchange (live entry refinement).
        # Separate from pending_orders (paper's simulated version) because a
        # live fill is irreversible and needs the exchange order id plus the
        # already-sized qty/leverage/risk to finalize without re-deriving them.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS live_pending_entries (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol            TEXT    NOT NULL,
                signal_id         INTEGER,
                direction         TEXT    NOT NULL,
                limit_price       REAL    NOT NULL,
                stop_loss         REAL    NOT NULL,
                take_profit       REAL    NOT NULL,
                position_size     REAL    NOT NULL,
                leverage          INTEGER NOT NULL,
                capital_at_risk   REAL    NOT NULL,
                risk_reward       REAL,
                model_version     TEXT,
                exchange_order_id TEXT    NOT NULL,
                created_ms        INTEGER NOT NULL,
                expiry_ms         INTEGER NOT NULL,
                status            TEXT    DEFAULT 'pending',  -- pending|filled|expired|cancelled|flattened
                filled_trade_id   INTEGER,
                created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # ── Live trades table ─────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS live_trades (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol                  TEXT    NOT NULL,
                signal_id               INTEGER,
                direction               TEXT,
                status                  TEXT    DEFAULT 'open',
                entry_price             REAL,
                stop_loss               REAL,
                take_profit             REAL,
                partial_tp_price        REAL,
                position_size           REAL,
                remaining_size          REAL,
                capital_at_risk         REAL,
                risk_reward             REAL,
                leverage                INTEGER DEFAULT 1,
                open_time               INTEGER,
                close_time              INTEGER,
                close_price             REAL,
                pnl                     REAL    DEFAULT 0,
                pnl_pct                 REAL    DEFAULT 0,
                partial_taken           INTEGER DEFAULT 0,
                partial_pnl             REAL    DEFAULT 0,
                max_favorable_excursion REAL,
                max_adverse_excursion   REAL,
                original_risk           REAL,
                exchange_entry_id       TEXT,
                exchange_sl_id          TEXT,
                exchange_tp_id          TEXT,
                model_version           TEXT,
                created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_live_trades_status
                ON live_trades(status)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_live_trades_symbol
                ON live_trades(symbol, status)
        """)

        # Fill-model provenance. Every row written before 2026-07-30 was
        # produced by filling market entries at sig.entry_price — a zone
        # *level*, not a price on offer — so 82% of them record outcomes from
        # trades that could not have happened (see realistic_entry_fill in
        # config.py). ML labels inherit that, so training data has to be
        # separable by fill model or the models learn the fiction.
        #
        # Deliberately NOT folded into ENGINE_VERSION / model_version: that
        # column gates the validated coin pool, and retagging it would
        # invalidate the pool as a side effect of an ML fix. This axis is
        # orthogonal — 'zone' = the old unobtainable fills, 'market' = fills at
        # the executable price.
        for table in ("feature_snapshots", "backtest_runs"):
            _safe_alter(conn, f"ALTER TABLE {table} ADD COLUMN fill_model TEXT")
            # Existing rows predate the fix by definition.
            conn.execute(
                f"UPDATE {table} SET fill_model='zone' WHERE fill_model IS NULL"
            )
        conn.commit()
    logger.info("Migrations complete.")
