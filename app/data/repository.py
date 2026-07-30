"""Data-access layer — all SQL queries live here."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pandas as pd

from app.db.connection import get_conn
from app.utils.logger import get_logger

logger = get_logger(__name__)


# ── Candle helpers ────────────────────────────────────────────────────────────

def upsert_candles(rows: list[dict[str, Any]]) -> int:
    """Insert-or-ignore candle rows. Returns number inserted."""
    if not rows:
        return 0
    sql = """
        INSERT OR IGNORE INTO candles
            (symbol, timeframe, open_time, open, high, low, close, volume,
             close_time, quote_volume, trades, taker_buy_base_volume,
             taker_buy_quote_volume)
        VALUES
            (:symbol, :timeframe, :open_time, :open, :high, :low, :close,
             :volume, :close_time, :quote_volume, :trades,
             :taker_buy_base_volume, :taker_buy_quote_volume)
    """
    with get_conn() as conn:
        conn.executemany(sql, rows)
        return conn.execute("SELECT changes()").fetchone()[0]


_CANDLE_COLS = ["open_time", "open", "high", "low", "close", "volume",
                "close_time", "quote_volume", "trades"]


def get_candles(
    symbol: str,
    timeframe: str,
    start_ms: int | None = None,
    end_ms: int | None = None,
    limit: int | None = None,
) -> pd.DataFrame:
    """
    Return candles as a DataFrame sorted by open_time ASC.

    When `limit` is given WITHOUT `start_ms`, the query fetches the
    most-recent N candles (DESC inner query) then re-sorts them ASC.
    This ensures you always get the latest data, not the oldest.
    """
    clauses = ["symbol = ?", "timeframe = ?"]
    params: list[Any] = [symbol, timeframe]

    if start_ms is not None:
        clauses.append("open_time >= ?")
        params.append(start_ms)
    if end_ms is not None:
        clauses.append("open_time <= ?")
        params.append(end_ms)

    where = " AND ".join(clauses)
    cols = "open_time, open, high, low, close, volume, close_time, quote_volume, trades"

    # When limit is set and no start_ms: grab the LAST N rows, then re-sort ASC
    if limit and start_ms is None:
        sql = f"""
            SELECT {cols} FROM (
                SELECT {cols} FROM candles
                WHERE {where}
                ORDER BY open_time DESC
                LIMIT {limit}
            ) ORDER BY open_time ASC
        """
    else:
        sql = f"""
            SELECT {cols} FROM candles
            WHERE {where}
            ORDER BY open_time ASC
        """
        if limit:
            sql += f" LIMIT {limit}"

    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()

    if not rows:
        return pd.DataFrame(columns=_CANDLE_COLS)

    df = pd.DataFrame(rows, columns=_CANDLE_COLS)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.set_index("open_time")
    return df


def get_candles_raw(
    symbol: str,
    timeframe: str,
    start_ms: int | None = None,
    end_ms: int | None = None,
) -> list[sqlite3.Row]:
    clauses = ["symbol = ?", "timeframe = ?"]
    params: list[Any] = [symbol, timeframe]
    if start_ms is not None:
        clauses.append("open_time >= ?")
        params.append(start_ms)
    if end_ms is not None:
        clauses.append("open_time <= ?")
        params.append(end_ms)
    where = " AND ".join(clauses)
    sql = f"""
        SELECT * FROM candles WHERE {where} ORDER BY open_time ASC
    """
    with get_conn() as conn:
        return conn.execute(sql, params).fetchall()


def get_latest_candle_time(symbol: str, timeframe: str) -> int | None:
    """Return the most recent open_time (ms) stored, or None."""
    sql = "SELECT MAX(open_time) FROM candles WHERE symbol=? AND timeframe=?"
    with get_conn() as conn:
        row = conn.execute(sql, (symbol, timeframe)).fetchone()
    return row[0] if row and row[0] is not None else None


def get_earliest_candle_time(symbol: str, timeframe: str) -> int | None:
    sql = "SELECT MIN(open_time) FROM candles WHERE symbol=? AND timeframe=?"
    with get_conn() as conn:
        row = conn.execute(sql, (symbol, timeframe)).fetchone()
    return row[0] if row and row[0] is not None else None


def count_candles(symbol: str, timeframe: str,
                  start_ms: int | None = None, end_ms: int | None = None) -> int:
    clauses = ["symbol=?", "timeframe=?"]
    params: list[Any] = [symbol, timeframe]
    if start_ms:
        clauses.append("open_time >= ?")
        params.append(start_ms)
    if end_ms:
        clauses.append("open_time <= ?")
        params.append(end_ms)
    sql = f"SELECT COUNT(*) FROM candles WHERE {' AND '.join(clauses)}"
    with get_conn() as conn:
        return conn.execute(sql, params).fetchone()[0]


# ── Zone helpers ──────────────────────────────────────────────────────────────

def upsert_zone(zone: dict[str, Any]) -> int:
    sql = """
        INSERT INTO zones
            (symbol, timeframe, zone_type, zone_top, zone_bottom,
             displacement_candle_time, score, rating, status,
             score_breakdown)
        VALUES
            (:symbol, :timeframe, :zone_type, :zone_top, :zone_bottom,
             :displacement_candle_time, :score, :rating, :status,
             :score_breakdown)
    """
    with get_conn() as conn:
        conn.execute(sql, zone)
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def get_active_zones(symbol: str, timeframe: str, zone_type: str | None = None) -> list[dict]:
    clauses = ["symbol=?", "timeframe=?", "status='active'"]
    params: list[Any] = [symbol, timeframe]
    if zone_type:
        clauses.append("zone_type=?")
        params.append(zone_type)
    sql = f"SELECT * FROM zones WHERE {' AND '.join(clauses)} ORDER BY score DESC"
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def mark_zone_mitigated(zone_id: int) -> None:
    sql = "UPDATE zones SET status='mitigated', updated_at=CURRENT_TIMESTAMP WHERE id=?"
    with get_conn() as conn:
        conn.execute(sql, (zone_id,))


# ── Signal helpers ────────────────────────────────────────────────────────────

def save_signal(signal: dict[str, Any]) -> int:
    signal = {**signal}
    for field in ("reasons", "warnings"):
        if isinstance(signal.get(field), list):
            signal[field] = json.dumps(signal[field])
    sql = """
        INSERT INTO signals
            (symbol, signal, confidence, market_regime, daily_bias,
             h4_bias, h1_confirmation, m30_zone_type, zone_id, zone_score,
             zone_rating, liquidity_sweep, premium_discount, entry_price,
             stop_loss, take_profit, risk_reward, setup_type,
             reasons, warnings, rejection_reason, model_version, data_quality, timestamp)
        VALUES
            (:symbol, :signal, :confidence, :market_regime, :daily_bias,
             :h4_bias, :h1_confirmation, :m30_zone_type, :zone_id, :zone_score,
             :zone_rating, :liquidity_sweep, :premium_discount, :entry_price,
             :stop_loss, :take_profit, :risk_reward, :setup_type,
             :reasons, :warnings, :rejection_reason, :model_version, :data_quality, :timestamp)
    """
    with get_conn() as conn:
        conn.execute(sql, signal)
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def get_latest_signal(symbol: str) -> dict | None:
    sql = "SELECT * FROM signals WHERE symbol=? ORDER BY timestamp DESC LIMIT 1"
    with get_conn() as conn:
        row = conn.execute(sql, (symbol,)).fetchone()
    if not row:
        return None
    d = dict(row)
    for field in ("reasons", "warnings"):
        if d.get(field):
            try:
                d[field] = json.loads(d[field])
            except Exception:
                pass
    return d


# ── Paper trade helpers ───────────────────────────────────────────────────────

def open_paper_trade(trade: dict[str, Any]) -> int:
    sql = """
        INSERT INTO paper_trades
            (symbol, signal_id, direction, status, entry_price, stop_loss,
             take_profit, position_size, capital_at_risk, risk_reward,
             open_time, model_version, original_risk, leverage)
        VALUES
            (:symbol, :signal_id, :direction, 'open', :entry_price, :stop_loss,
             :take_profit, :position_size, :capital_at_risk, :risk_reward,
             :open_time, :model_version, :original_risk, :leverage)
    """
    with get_conn() as conn:
        conn.execute(sql, trade)
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def update_open_paper_trade_pnl(trade_id: int, unrealized_pnl: float, pnl_pct: float,
                                 mfe: float, mae: float) -> None:
    sql = """
        UPDATE paper_trades
        SET pnl=?, pnl_pct=?, max_favorable_excursion=?, max_adverse_excursion=?,
            updated_at=CURRENT_TIMESTAMP
        WHERE id=? AND status='open'
    """
    with get_conn() as conn:
        conn.execute(sql, (unrealized_pnl, pnl_pct, mfe, mae, trade_id))


def close_paper_trade(trade_id: int, close_price: float, close_time: int,
                       pnl: float, pnl_pct: float, status: str,
                       mfe: float = 0.0, mae: float = 0.0) -> None:
    sql = """
        UPDATE paper_trades
        SET close_price=?, close_time=?, pnl=?, pnl_pct=?, status=?,
            max_favorable_excursion=?, max_adverse_excursion=?,
            updated_at=CURRENT_TIMESTAMP
        WHERE id=?
    """
    with get_conn() as conn:
        conn.execute(sql, (close_price, close_time, pnl, pnl_pct, status, mfe, mae, trade_id))


def get_recent_closed_paper_trades(symbol: str, n: int = 5) -> list[dict]:
    """Return the n most-recent closed paper trades for symbol, oldest first."""
    sql = """
        SELECT pnl FROM paper_trades
        WHERE symbol=? AND status != 'open'
        ORDER BY close_time DESC LIMIT ?
    """
    with get_conn() as conn:
        rows = conn.execute(sql, (symbol, n)).fetchall()
    return [{"pnl": r[0]} for r in reversed(rows)]


def get_open_paper_trades(symbol: str) -> list[dict]:
    sql = "SELECT * FROM paper_trades WHERE symbol=? AND status='open'"
    with get_conn() as conn:
        rows = conn.execute(sql, (symbol,)).fetchall()
    return [dict(r) for r in rows]


def get_all_open_paper_trades() -> list[dict]:
    """All open trades across every symbol — used for cross-process portfolio caps."""
    sql = "SELECT * FROM paper_trades WHERE status='open'"
    with get_conn() as conn:
        rows = conn.execute(sql).fetchall()
    return [dict(r) for r in rows]


def get_total_closed_pnl() -> float:
    """Sum of realized PnL across all closed trades (all symbols)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(pnl), 0) FROM paper_trades WHERE status != 'open'"
        ).fetchone()
    return float(row[0]) if row else 0.0


def apply_partial_tp(trade_id: int, new_size: float, partial_pnl: float) -> None:
    """Record a partial take-profit: shrink the position, store realized PnL."""
    with get_conn() as conn:
        conn.execute(
            """UPDATE paper_trades
               SET position_size=?, partial_taken=1, partial_pnl=?,
                   updated_at=CURRENT_TIMESTAMP
               WHERE id=?""",
            (round(new_size, 6), round(partial_pnl, 2), trade_id),
        )


def update_paper_trade_stop(trade_id: int, stop_loss: float, ratchet_level: int) -> None:
    """Persist a ratcheted stop-loss level (only ever tightens, never loosens)."""
    with get_conn() as conn:
        conn.execute(
            """UPDATE paper_trades
               SET stop_loss=?, ratchet_level=?, updated_at=CURRENT_TIMESTAMP
               WHERE id=? AND status='open'""",
            (round(stop_loss, 6), ratchet_level, trade_id),
        )


# ── Funding rates ──────────────────────────────────────────────────────────────

def save_funding_rates(symbol: str, rows: list[dict]) -> int:
    """Upsert historical funding rates. Returns number of rows written."""
    if not rows:
        return 0
    with get_conn() as conn:
        conn.executemany(
            """INSERT OR IGNORE INTO funding_rates (symbol, funding_time, rate)
               VALUES (?, ?, ?)""",
            [(symbol.upper(), r["funding_time"], r["rate"]) for r in rows],
        )
    return len(rows)


def get_funding_rate_at(symbol: str, ts_ms: int) -> float | None:
    """
    Latest stored funding rate at/just before ts_ms, or None when no data
    within the prior 24h (stale data must not gate decisions).
    """
    with get_conn() as conn:
        row = conn.execute(
            """SELECT rate FROM funding_rates
               WHERE symbol = ? AND funding_time <= ? AND funding_time >= ?
               ORDER BY funding_time DESC LIMIT 1""",
            (symbol.upper(), ts_ms, ts_ms - 24 * 3_600_000),
        ).fetchone()
    return float(row[0]) if row else None


# ── Pending limit orders ───────────────────────────────────────────────────────

def create_pending_order(d: dict) -> int:
    sql = """
        INSERT INTO pending_orders
            (symbol, signal_id, direction, limit_price, stop_loss, take_profit,
             risk_reward, model_version, created_ms, expiry_ms, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
    """
    with get_conn() as conn:
        cur = conn.execute(sql, (
            d["symbol"], d.get("signal_id"), d["direction"], d["limit_price"],
            d["stop_loss"], d["take_profit"], d.get("risk_reward"),
            d.get("model_version"), d["created_ms"], d["expiry_ms"],
        ))
        return cur.lastrowid


def get_pending_orders(symbol: str | None = None) -> list[dict]:
    with get_conn() as conn:
        if symbol:
            rows = conn.execute(
                "SELECT * FROM pending_orders WHERE status='pending' AND symbol=?",
                (symbol,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM pending_orders WHERE status='pending'"
            ).fetchall()
    return [dict(r) for r in rows]


def update_pending_order_status(
    order_id: int, status: str, filled_trade_id: int | None = None
) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE pending_orders SET status=?, filled_trade_id=? WHERE id=?",
            (status, filled_trade_id, order_id),
        )


# ── Live pending entries (real resting limit orders) ───────────────────────────

def create_live_pending_entry(d: dict) -> int:
    sql = """
        INSERT INTO live_pending_entries
            (symbol, signal_id, direction, limit_price, stop_loss, take_profit,
             position_size, leverage, capital_at_risk, risk_reward, model_version,
             exchange_order_id, created_ms, expiry_ms, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
    """
    with get_conn() as conn:
        cur = conn.execute(sql, (
            d["symbol"], d.get("signal_id"), d["direction"], d["limit_price"],
            d["stop_loss"], d["take_profit"], d["position_size"], d["leverage"],
            d["capital_at_risk"], d.get("risk_reward"), d.get("model_version"),
            d["exchange_order_id"], d["created_ms"], d["expiry_ms"],
        ))
        return cur.lastrowid


def get_live_pending_entries(symbol: str | None = None) -> list[dict]:
    with get_conn() as conn:
        if symbol:
            rows = conn.execute(
                "SELECT * FROM live_pending_entries WHERE status='pending' AND symbol=?",
                (symbol,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM live_pending_entries WHERE status='pending'"
            ).fetchall()
    return [dict(r) for r in rows]


def get_live_pending_entry_by_order_id(exchange_order_id: str) -> dict | None:
    """Look up regardless of status — a late fill that raced an expiry cancel
    must still be found and finalized, never silently dropped."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM live_pending_entries WHERE exchange_order_id=?",
            (exchange_order_id,),
        ).fetchone()
    return dict(row) if row else None


def update_live_pending_entry_status(
    entry_id: int, status: str, filled_trade_id: int | None = None
) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE live_pending_entries SET status=?, filled_trade_id=? WHERE id=?",
            (status, filled_trade_id, entry_id),
        )


def get_closed_pnl_since(since_ms: int) -> float:
    """
    Sum of realized PnL for trades closed at/after since_ms (all symbols).
    typeof() guard: legacy rows stored close_time as TEXT, which SQLite
    sorts above every INTEGER and would wrongly match.
    """
    with get_conn() as conn:
        row = conn.execute(
            """SELECT COALESCE(SUM(pnl), 0) FROM paper_trades
               WHERE status != 'open'
                 AND typeof(close_time) = 'integer'
                 AND close_time >= ?""",
            (since_ms,),
        ).fetchone()
    return float(row[0]) if row else 0.0


def get_last_stopped_trade_ms(symbol: str) -> int | None:
    """Return close_time (ms) of the most recent stopped-out trade for symbol, or None."""
    sql = """
        SELECT close_time FROM paper_trades
        WHERE symbol=? AND status='stopped'
        ORDER BY close_time DESC LIMIT 1
    """
    with get_conn() as conn:
        row = conn.execute(sql, (symbol,)).fetchone()
    if row and row[0]:
        val = row[0]
        if isinstance(val, (int, float)):
            return int(val)
        try:
            return int(val)
        except (ValueError, TypeError):
            return None
    return None


def get_recent_stops_in_window(symbol: str, window_ms: int) -> int:
    """Return count of stopped-out trades for symbol within the last window_ms milliseconds."""
    cutoff_ms = int(__import__("time").time() * 1000) - window_ms
    sql = """
        SELECT COUNT(*) FROM paper_trades
        WHERE symbol=? AND status='stopped' AND close_time >= ?
    """
    with get_conn() as conn:
        row = conn.execute(sql, (symbol, cutoff_ms)).fetchone()
    return int(row[0]) if row else 0


def get_portfolio_stops_in_window(window_ms: int) -> int:
    """Return count of stopped-out trades across ALL symbols within the last window_ms ms."""
    cutoff_ms = int(__import__("time").time() * 1000) - window_ms
    sql = """
        SELECT COUNT(*) FROM paper_trades
        WHERE status='stopped' AND close_time >= ?
    """
    with get_conn() as conn:
        row = conn.execute(sql, (cutoff_ms,)).fetchone()
    return int(row[0]) if row else 0


def get_symbol_risk_stats(symbol: str, window_ms: int, now_ms: int | None = None) -> tuple[int, float]:
    """
    Per-symbol risk memory over a rolling window: (stops, realized_pnl).

    Generalises the intraday stop cooldowns to a multi-day horizon so a symbol
    that keeps stopping out — even with entries spaced days apart, like RIFUSDT —
    can be benched. `now_ms` lets backtests/tests pass a deterministic clock
    (defaults to wall time).
    """
    ref_ms = now_ms if now_ms is not None else int(__import__("time").time() * 1000)
    cutoff_ms = ref_ms - window_ms
    sql = """
        SELECT
            COALESCE(SUM(CASE WHEN status='stopped' THEN 1 ELSE 0 END), 0) AS stops,
            COALESCE(SUM(pnl), 0.0) AS realized
        FROM paper_trades
        WHERE symbol=? AND status != 'open' AND close_time >= ?
    """
    with get_conn() as conn:
        row = conn.execute(sql, (symbol, cutoff_ms)).fetchone()
    if not row:
        return 0, 0.0
    return int(row[0]), float(row[1])


def get_zone_blacklist(symbol: str) -> list[dict]:
    """Return active (non-expired) blacklisted zone price levels for a symbol."""
    import json, time
    val = get_setting(f"zone_bl_{symbol.lower()}")
    if not val:
        return []
    try:
        zones = json.loads(val)
        now_ms = int(time.time() * 1000)
        return [z for z in zones if z.get("expiry_ms", 0) > now_ms]
    except Exception:
        return []


def add_zone_blacklist(symbol: str, entry_price: float, duration_ms: int = 24 * 60 * 60 * 1000) -> None:
    """Blacklist a price zone after a stop-out (default 24 hours)."""
    import json, time
    active = get_zone_blacklist(symbol)
    now_ms = int(time.time() * 1000)
    active.append({"price": round(entry_price, 6), "expiry_ms": now_ms + duration_ms})
    set_setting(f"zone_bl_{symbol.lower()}", json.dumps(active))


def is_zone_blacklisted(symbol: str, entry_price: float, tolerance_pct: float = 0.02) -> bool:
    """Return True if entry_price falls within 2% of any active blacklisted zone."""
    if entry_price <= 0:
        return False
    for z in get_zone_blacklist(symbol):
        if abs(entry_price - z["price"]) / entry_price <= tolerance_pct:
            return True
    return False


def get_paper_trade_summary(symbol: str | None = None) -> dict:
    """
    Aggregate paper trade stats. Pass symbol to filter to one coin,
    or None / omit to aggregate across all symbols.
    """
    if symbol:
        sql = """
            SELECT
                COUNT(*)                                   AS total,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END)  AS wins,
                SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END)  AS losses,
                SUM(pnl)                                   AS total_pnl,
                MAX(pnl)                                   AS best_trade,
                MIN(pnl)                                   AS worst_trade
            FROM paper_trades
            WHERE symbol=? AND status != 'open'
        """
        with get_conn() as conn:
            row = conn.execute(sql, (symbol,)).fetchone()
    else:
        sql = """
            SELECT
                COUNT(*)                                   AS total,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END)  AS wins,
                SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END)  AS losses,
                SUM(pnl)                                   AS total_pnl,
                MAX(pnl)                                   AS best_trade,
                MIN(pnl)                                   AS worst_trade
            FROM paper_trades
            WHERE status != 'open'
        """
        with get_conn() as conn:
            row = conn.execute(sql).fetchone()
    return dict(row) if row else {}


# ── Backtest helpers ──────────────────────────────────────────────────────────

def save_backtest_run(run: dict[str, Any]) -> int:
    if isinstance(run.get("metrics"), dict):
        run = {**run, "metrics": json.dumps(run["metrics"])}
    run = {"fill_model": current_fill_model(), **run}
    sql = """
        INSERT INTO backtest_runs
            (symbol, start_time, end_time, initial_capital, final_capital,
             total_trades, win_rate, profit_factor, max_drawdown, net_profit,
             metrics, model_version, fill_model)
        VALUES
            (:symbol, :start_time, :end_time, :initial_capital, :final_capital,
             :total_trades, :win_rate, :profit_factor, :max_drawdown,
             :net_profit, :metrics, :model_version, :fill_model)
    """
    with get_conn() as conn:
        conn.execute(sql, run)
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def save_backtest_trades(trades: list[dict[str, Any]]) -> None:
    for t in trades:
        if isinstance(t.get("features"), dict):
            t["features"] = json.dumps(t["features"])
    sql = """
        INSERT INTO backtest_trades
            (backtest_run_id, symbol, direction, entry_price, exit_price,
             stop_loss, take_profit, position_size, pnl, pnl_pct,
             entry_time, exit_time, exit_reason, max_favorable_excursion,
             max_adverse_excursion, signal_confidence, zone_score, setup_type,
             features)
        VALUES
            (:backtest_run_id, :symbol, :direction, :entry_price, :exit_price,
             :stop_loss, :take_profit, :position_size, :pnl, :pnl_pct,
             :entry_time, :exit_time, :exit_reason, :max_favorable_excursion,
             :max_adverse_excursion, :signal_confidence, :zone_score,
             :setup_type, :features)
    """
    with get_conn() as conn:
        conn.executemany(sql, trades)


def get_backtest_runs(symbol: str, limit: int = 10) -> list[dict]:
    sql = """SELECT * FROM backtest_runs WHERE symbol=?
             ORDER BY created_at DESC LIMIT ?"""
    with get_conn() as conn:
        rows = conn.execute(sql, (symbol, limit)).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        if d.get("metrics"):
            try:
                d["metrics"] = json.loads(d["metrics"])
            except Exception:
                pass
        result.append(d)
    return result


# ── Model version helpers ─────────────────────────────────────────────────────

def save_model_version(mv: dict[str, Any]) -> int:
    for field in ("features", "hyperparameters"):
        if isinstance(mv.get(field), (list, dict)):
            mv = {**mv, field: json.dumps(mv[field])}
    sql = """
        INSERT OR REPLACE INTO model_versions
            (symbol, model_type, version, algorithm, features,
             hyperparameters, file_path, status)
        VALUES
            (:symbol, :model_type, :version, :algorithm, :features,
             :hyperparameters, :file_path, :status)
    """
    with get_conn() as conn:
        conn.execute(sql, mv)
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def get_active_model(symbol: str, model_type: str) -> dict | None:
    sql = """SELECT * FROM model_versions
             WHERE symbol=? AND model_type=? AND status='active'
             ORDER BY created_at DESC LIMIT 1"""
    with get_conn() as conn:
        row = conn.execute(sql, (symbol, model_type)).fetchone()
    if not row:
        return None
    d = dict(row)
    for field in ("features", "hyperparameters"):
        if d.get(field):
            try:
                d[field] = json.loads(d[field])
            except Exception:
                pass
    return d


# ── Feature snapshot helpers ──────────────────────────────────────────────────

def current_fill_model() -> str:
    """'market' when entries fill at the executable price, 'zone' when they fill
    at the signal's zone level. Stamped on every training label so the models
    are never fit on outcomes from fills that could not have happened."""
    from app.config import get_settings
    return "market" if get_settings().realistic_entry_fill else "zone"


def save_feature_snapshot(snap: dict[str, Any]) -> None:
    if isinstance(snap.get("features"), dict):
        snap = {**snap, "features": json.dumps(snap["features"])}
    snap = {"fill_model": current_fill_model(), **snap}
    sql = """
        INSERT INTO feature_snapshots
            (signal_id, symbol, timestamp, features, outcome, pnl, fill_model)
        VALUES
            (:signal_id, :symbol, :timestamp, :features, :outcome, :pnl, :fill_model)
    """
    with get_conn() as conn:
        conn.execute(sql, snap)


def get_feature_snapshots(
    symbol: str,
    outcome: str | None = None,
    fill_model: str | None = "market",
) -> list[dict]:
    """Labeled training rows for a symbol.

    fill_model defaults to 'market' so callers get only labels whose outcome
    came from an obtainable entry price. Pass None to read every row regardless
    of provenance (audits and back-compat reporting only — never training)."""
    clauses = ["symbol=?"]
    params: list[Any] = [symbol]
    if outcome:
        clauses.append("outcome=?")
        params.append(outcome)
    if fill_model:
        clauses.append("fill_model=?")
        params.append(fill_model)
    sql = f"SELECT * FROM feature_snapshots WHERE {' AND '.join(clauses)} ORDER BY timestamp ASC"
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        if d.get("features"):
            try:
                d["features"] = json.loads(d["features"])
            except Exception:
                pass
        result.append(d)
    return result


# ── App settings helpers ──────────────────────────────────────────────────────

def get_setting(key: str, default: str = "") -> str:
    sql = "SELECT value FROM app_settings WHERE key=?"
    with get_conn() as conn:
        row = conn.execute(sql, (key,)).fetchone()
    return row[0] if row else default


def set_setting(key: str, value: str) -> None:
    sql = """INSERT INTO app_settings(key, value, updated_at)
             VALUES(?,?,CURRENT_TIMESTAMP)
             ON CONFLICT(key) DO UPDATE SET value=excluded.value,
             updated_at=CURRENT_TIMESTAMP"""
    with get_conn() as conn:
        conn.execute(sql, (key, value))


# ── Data quality ──────────────────────────────────────────────────────────────

def save_data_quality_report(report: dict[str, Any]) -> None:
    if isinstance(report.get("issues"), list):
        report = {**report, "issues": json.dumps(report["issues"])}
    sql = """
        INSERT INTO data_quality_reports
            (symbol, timeframe, start_time, end_time, expected_candles,
             actual_candles, missing_candles, quality_score, issues)
        VALUES
            (:symbol, :timeframe, :start_time, :end_time, :expected_candles,
             :actual_candles, :missing_candles, :quality_score, :issues)
    """
    with get_conn() as conn:
        conn.execute(sql, report)


def get_latest_data_quality(symbol: str, timeframe: str) -> dict | None:
    sql = """SELECT * FROM data_quality_reports
             WHERE symbol=? AND timeframe=?
             ORDER BY created_at DESC LIMIT 1"""
    with get_conn() as conn:
        row = conn.execute(sql, (symbol, timeframe)).fetchone()
    if not row:
        return None
    d = dict(row)
    if d.get("issues"):
        try:
            d["issues"] = json.loads(d["issues"])
        except Exception:
            pass
    return d
