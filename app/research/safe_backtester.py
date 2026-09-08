"""Strict research backtester with look-ahead-safe historical quote selection.

Use this class for new research. It subclasses the event engine so the quote
policy can be hardened without changing the production LQ-MTF backtester.
"""

from __future__ import annotations

from bisect import bisect_right

from app.research.backtester import (
    BacktestDataError,
    BookQuote,
    ResearchBacktester as _ResearchBacktester,
)


class ResearchBacktester(_ResearchBacktester):
    """Research engine that never consumes a quote from the future."""

    def _quote_for(self, timestamp: int, fallback_mid: float) -> BookQuote:
        # Historical execution may only use information known by `timestamp`.
        # The base implementation originally selected the first quote >= the
        # event, which can leak a future quote. Select the latest quote <= event.
        idx = bisect_right(self._quote_times, timestamp) - 1
        if idx >= 0:
            quote = self.book_quotes[idx]
            age_ms = timestamp - quote.timestamp
            if 0 <= age_ms <= self.config.quote_freshness_ms:
                if quote.bid <= 0 or quote.ask <= 0 or quote.ask < quote.bid:
                    raise BacktestDataError("invalid book quote")
                return quote

        if self.config.require_book:
            raise BacktestDataError("missing_or_stale_book_quote")

        half = self.config.assumed_spread_bps / 20_000.0
        return BookQuote(
            timestamp=timestamp,
            bid=fallback_mid * (1 - half),
            ask=fallback_mid * (1 + half),
        )
