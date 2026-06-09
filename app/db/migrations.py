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
    logger.info("Migrations complete.")
