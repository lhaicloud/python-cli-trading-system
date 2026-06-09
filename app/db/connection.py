"""SQLite connection and schema initialisation."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from app.config import get_settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

_SCHEMA_FILE = Path(__file__).parent / "schema.sql"


def get_db_path() -> Path:
    return get_settings().db_path


def init_db() -> None:
    """Create all tables if they don't exist."""
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    schema_sql = _SCHEMA_FILE.read_text(encoding="utf-8")
    with sqlite3.connect(db_path) as conn:
        conn.executescript(schema_sql)
    logger.debug("Database initialised at %s", db_path)


@contextmanager
def get_conn() -> Generator[sqlite3.Connection, None, None]:
    """Yield a WAL-enabled SQLite connection with row_factory."""
    db_path = get_db_path()
    conn = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
