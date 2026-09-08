"""Data-quality validation for candle data."""

from __future__ import annotations

from app.data.repository import (
    count_candles,
    get_earliest_candle_time,
    get_latest_candle_time,
    save_data_quality_report,
)
from app.utils.logger import get_logger
from app.utils.timeframes import expected_candle_count, tf_to_ms

logger = get_logger(__name__)


def validate_candles(symbol: str, timeframe: str,
                     start_ms: int, end_ms: int) -> dict:
    """
    Check data completeness for a symbol/timeframe window.

    Returns a report dict including quality_score (0–100).
    """
    actual = count_candles(symbol, timeframe, start_ms, end_ms)
    expected = expected_candle_count(start_ms, end_ms, timeframe)
    missing = max(0, expected - actual)
    quality_score = 100.0 if expected == 0 else max(0.0, 100 * actual / expected)

    issues = []
    if missing > 0:
        issues.append(f"{missing} missing candles out of {expected} expected")
    if quality_score < 95:
        issues.append(f"Data completeness {quality_score:.1f}% — backtest results may be unreliable")
    if quality_score < 80:
        issues.append("CRITICAL: Less than 80% data coverage — do not use for signals")

    report = {
        "symbol": symbol,
        "timeframe": timeframe,
        "start_time": start_ms,
        "end_time": end_ms,
        "expected_candles": expected,
        "actual_candles": actual,
        "missing_candles": missing,
        "quality_score": round(quality_score, 2),
        "issues": issues,
    }
    save_data_quality_report(report)
    logger.info(
        "[%s %s] Data quality %.1f%% — %d/%d candles",
        symbol, timeframe, quality_score, actual, expected,
    )
    return report


def validate_all_timeframes(symbol: str, timeframes: list[str],
                              start_ms: int, end_ms: int) -> dict[str, dict]:
    return {tf: validate_candles(symbol, tf, start_ms, end_ms) for tf in timeframes}


def is_timestamp_fresh(latest_ms: int, now_ms: int, timeframe: str) -> bool:
    """Return True only when a stored candle timestamp is plausibly fresh.

    The threshold is three timeframe intervals. Future timestamps fail closed.
    """
    interval_ms = tf_to_ms(timeframe)
    age_ms = now_ms - latest_ms
    return 0 <= age_ms < interval_ms * 3


def data_is_sufficient(symbol: str, timeframe: str,
                        lookback_ms: int = 7 * 86_400_000) -> bool:
    """Fail closed when the latest stored candle is stale.

    `lookback_ms` is retained for call-site compatibility but is deliberately
    not used as an alternate freshness window. The old implementation accepted
    data up to seven days old for every timeframe because it used an ``or``
    condition against the generic lookback. That could mark stale 30m/1h data
    as sufficient.
    """
    del lookback_ms

    latest = get_latest_candle_time(symbol, timeframe)
    if latest is None:
        return False

    import time as _time

    now_ms = int(_time.time() * 1000)
    return is_timestamp_fresh(latest, now_ms, timeframe)
