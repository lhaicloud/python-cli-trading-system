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
        conn.commit()
    logger.info("Migrations complete.")
