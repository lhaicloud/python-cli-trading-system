"""Verify the strict research engine never consumes a future book quote."""

from app.research.backtester import BookQuote, MarketBar, ResearchBacktestConfig, TradeIntent, Direction
from app.research.safe_backtester import ResearchBacktester


def bar(t: int, o: float, h: float, l: float, c: float) -> MarketBar:
    return MarketBar(t, t + 999, o, h, l, c)


bars = [
    bar(0, 100, 101, 99, 100),
    bar(1000, 100, 105, 95, 102),
    bar(2000, 102, 110, 101, 108),
]
intent = TradeIntent(
    intent_id="quote-safety",
    strategy_id="synthetic",
    strategy_version="1",
    symbol="BTCUSDT",
    asset_class="crypto",
    direction=Direction.LONG,
    signal_time=999,
    signal_price=100,
    stop_loss=90,
    take_profit=110,
    quantity=1,
)
config = ResearchBacktestConfig(
    require_book=True,
    require_funding=False,
    quote_freshness_ms=100,
    min_net_reward_risk=0.5,
    max_entry_drift_bps=100,
    slippage_bps=0,
)

# A quote at 1001 must NOT be used for an entry at 1000.
future_only = ResearchBacktester(
    trade_bars=bars,
    book_quotes=[BookQuote(1001, 99.95, 100.05)],
    config=config,
).run([intent])
assert not future_only.trades
assert any("missing_or_stale_book_quote" in r.reason for r in future_only.rejections)

# A quote at-or-before the event is valid when fresh. Include exit-time quotes
# so a completed trade can also close without falling back to future data.
known_quotes = ResearchBacktester(
    trade_bars=bars,
    book_quotes=[
        BookQuote(999, 99.95, 100.05),
        BookQuote(1999, 109.95, 110.05),
        BookQuote(2999, 109.95, 110.05),
    ],
    config=config,
).run([intent])
assert len(known_quotes.trades) == 1
print("ALL CHECKS PASSED")
