"""SQLite persistence for research-only market data and metadata history."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from app.data.instruments import InstrumentMetadata


class ResearchStore:
    """Small side-by-side schema that does not alter live trading tables."""

    def __init__(self, db: str | Path | sqlite3.Connection) -> None:
        self._owns_connection = not isinstance(db, sqlite3.Connection)
        self.conn = sqlite3.connect(str(db)) if self._owns_connection else db
        self.conn.row_factory = sqlite3.Row
        self.ensure_schema()

    def close(self) -> None:
        if self._owns_connection:
            self.conn.close()

    def __enter__(self) -> "ResearchStore":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def ensure_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS research_instrument_metadata (
                symbol TEXT NOT NULL,
                observed_at INTEGER NOT NULL,
                status TEXT NOT NULL,
                contract_type TEXT NOT NULL,
                asset_class TEXT NOT NULL,
                base_asset TEXT NOT NULL,
                quote_asset TEXT NOT NULL,
                margin_asset TEXT NOT NULL,
                tick_size REAL NOT NULL,
                step_size REAL NOT NULL,
                min_qty REAL NOT NULL,
                min_notional REAL,
                raw_json TEXT NOT NULL,
                PRIMARY KEY (symbol, observed_at)
            );

            CREATE TABLE IF NOT EXISTS research_price_klines (
                series_type TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                open_time INTEGER NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                close_time INTEGER NOT NULL,
                PRIMARY KEY (series_type, symbol, timeframe, open_time)
            );

            CREATE TABLE IF NOT EXISTS research_funding_rates (
                symbol TEXT NOT NULL,
                funding_time INTEGER NOT NULL,
                rate REAL NOT NULL,
                mark_price REAL,
                PRIMARY KEY (symbol, funding_time)
            );

            CREATE TABLE IF NOT EXISTS research_funding_info (
                symbol TEXT NOT NULL,
                observed_at INTEGER NOT NULL,
                interval_hours INTEGER,
                cap REAL,
                floor REAL,
                raw_json TEXT NOT NULL,
                PRIMARY KEY (symbol, observed_at)
            );

            CREATE TABLE IF NOT EXISTS research_book_tickers (
                symbol TEXT NOT NULL,
                captured_at INTEGER NOT NULL,
                bid REAL NOT NULL,
                bid_qty REAL NOT NULL,
                ask REAL NOT NULL,
                ask_qty REAL NOT NULL,
                PRIMARY KEY (symbol, captured_at)
            );

            CREATE INDEX IF NOT EXISTS idx_research_price_lookup
            ON research_price_klines(symbol, series_type, timeframe, open_time);

            CREATE INDEX IF NOT EXISTS idx_research_book_lookup
            ON research_book_tickers(symbol, captured_at);
            """
        )
        self.conn.commit()

    def save_instrument_metadata(
        self,
        metadata: InstrumentMetadata,
        observed_at: int,
        raw: dict[str, Any],
    ) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO research_instrument_metadata (
                symbol, observed_at, status, contract_type, asset_class,
                base_asset, quote_asset, margin_asset, tick_size, step_size,
                min_qty, min_notional, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                metadata.symbol,
                observed_at,
                metadata.status,
                metadata.contract_type,
                metadata.asset_class.value,
                metadata.base_asset,
                metadata.quote_asset,
                metadata.margin_asset,
                metadata.tick_size,
                metadata.step_size,
                metadata.min_qty,
                metadata.min_notional,
                json.dumps(raw, sort_keys=True, separators=(",", ":")),
            ),
        )
        self.conn.commit()

    def upsert_price_klines(
        self,
        series_type: str,
        symbol: str,
        timeframe: str,
        rows: Iterable[list[Any]],
    ) -> int:
        before = self.conn.total_changes
        payload = []
        for row in rows:
            if len(row) < 7:
                raise ValueError("malformed kline row")
            payload.append(
                (
                    series_type,
                    symbol.upper(),
                    timeframe,
                    int(row[0]),
                    float(row[1]),
                    float(row[2]),
                    float(row[3]),
                    float(row[4]),
                    int(row[6]),
                )
            )
        self.conn.executemany(
            """
            INSERT OR IGNORE INTO research_price_klines
            (series_type, symbol, timeframe, open_time, open, high, low, close, close_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payload,
        )
        self.conn.commit()
        return self.conn.total_changes - before

    def upsert_funding_rates(self, symbol: str, rows: Iterable[dict[str, Any]]) -> int:
        before = self.conn.total_changes
        payload = [
            (
                symbol.upper(),
                int(row["funding_time"]),
                float(row["rate"]),
                float(row["mark_price"]) if row.get("mark_price") not in (None, "") else None,
            )
            for row in rows
        ]
        self.conn.executemany(
            """
            INSERT OR REPLACE INTO research_funding_rates
            (symbol, funding_time, rate, mark_price) VALUES (?, ?, ?, ?)
            """,
            payload,
        )
        self.conn.commit()
        return self.conn.total_changes - before

    def save_funding_info(self, symbol: str, observed_at: int, raw: dict[str, Any]) -> None:
        interval = raw.get("fundingIntervalHours")
        cap = raw.get("adjustedFundingRateCap")
        floor = raw.get("adjustedFundingRateFloor")
        self.conn.execute(
            """
            INSERT OR REPLACE INTO research_funding_info
            (symbol, observed_at, interval_hours, cap, floor, raw_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                symbol.upper(),
                observed_at,
                int(interval) if interval not in (None, "") else None,
                float(cap) if cap not in (None, "") else None,
                float(floor) if floor not in (None, "") else None,
                json.dumps(raw, sort_keys=True, separators=(",", ":")),
            ),
        )
        self.conn.commit()

    def save_book_ticker(
        self,
        symbol: str,
        captured_at: int,
        bid: float,
        bid_qty: float,
        ask: float,
        ask_qty: float,
    ) -> int:
        if bid <= 0 or ask <= 0 or ask < bid:
            raise ValueError("invalid bid/ask observation")
        symbol = symbol.upper()
        row = self.conn.execute(
            "SELECT MAX(captured_at) AS last_ts FROM research_book_tickers WHERE symbol=?",
            (symbol,),
        ).fetchone()
        last_ts = int(row["last_ts"]) if row and row["last_ts"] is not None else -1
        unique_ts = max(int(captured_at), last_ts + 1)
        self.conn.execute(
            """
            INSERT INTO research_book_tickers
            (symbol, captured_at, bid, bid_qty, ask, ask_qty)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (symbol, unique_ts, float(bid), float(bid_qty), float(ask), float(ask_qty)),
        )
        self.conn.commit()
        return unique_ts
