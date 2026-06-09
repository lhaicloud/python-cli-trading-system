"""Timeframe utilities — conversions, interval ms, ordering."""

from __future__ import annotations

from datetime import datetime, timezone

# Binance interval strings → milliseconds
_TF_MS: dict[str, int] = {
    "1m":  60_000,
    "5m":  300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h":  3_600_000,
    "2h":  7_200_000,
    "4h":  14_400_000,
    "6h":  21_600_000,
    "8h":  28_800_000,
    "12h": 43_200_000,
    "1d":  86_400_000,
    "3d":  259_200_000,
    "1w":  604_800_000,
}

_TF_ORDER = list(_TF_MS.keys())


def tf_to_ms(timeframe: str) -> int:
    """Return milliseconds for a Binance timeframe string."""
    if timeframe not in _TF_MS:
        raise ValueError(f"Unknown timeframe: {timeframe!r}. Valid: {list(_TF_MS)}")
    return _TF_MS[timeframe]


def ms_to_dt(ms: int) -> datetime:
    """Convert Unix milliseconds to UTC datetime."""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def dt_to_ms(dt: datetime) -> int:
    """Convert datetime (any tz) to Unix milliseconds."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def str_to_ms(date_str: str) -> int:
    """Parse 'YYYY-MM-DD' or ISO string to Unix ms (UTC)."""
    from dateutil.parser import parse
    dt = parse(date_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def expected_candle_count(start_ms: int, end_ms: int, timeframe: str) -> int:
    """Approximate number of candles between two timestamps."""
    interval = tf_to_ms(timeframe)
    return max(0, (end_ms - start_ms) // interval)


def is_higher_tf(tf_a: str, tf_b: str) -> bool:
    """Return True if tf_a has a longer interval than tf_b."""
    return tf_to_ms(tf_a) > tf_to_ms(tf_b)


def sort_timeframes(timeframes: list[str]) -> list[str]:
    """Sort timeframes from shortest to longest."""
    return sorted(timeframes, key=lambda t: _TF_MS.get(t, 0))
