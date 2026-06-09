"""Custom exceptions for LQ-MTF."""

from __future__ import annotations


class DataNotReadyError(RuntimeError):
    """Raised when required timeframe data has not been backfilled yet."""
