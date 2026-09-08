"""No-network checks for candidate research strategy families."""

from app.research.backtester import Direction, MarketBar
from app.research.strategies import (
    FundingAwareMomentumStrategy,
    HigherTimeframeTrendStrategy,
    MeanReversionStrategy,
    PullbackTrendStrategy,
    RegimeAdaptiveStrategy,
    StrategyContext,
    TrendBreakoutStrategy,
    VolatilityMomentumStrategy,
    cross_sectional_momentum_rank,
)


def bar(i: int, close: float, spread: float = 0.5, open_offset: float = -0.2) -> MarketBar:
    o = close + open_offset
    return MarketBar(i * 1000, i * 1000 + 999, o, max(o, close) + spread, min(o, close) - spread, close)


ctx = StrategyContext("BTCUSDT", "crypto", 1.0)
trend = [bar(i, 100 + i) for i in range(90)]

breakout = TrendBreakoutStrategy().generate(trend, ctx)
assert breakout, "trend breakout should generate candidates on synthetic trend"
assert all(x.signal_time in {b.close_time for b in trend} for x in breakout)
assert all(x.strategy_id == "trend_breakout" for x in breakout)

# Completed higher-timeframe bars only: a future HTF bar must not be required.
htf = [bar(i * 4, 100 + i * 2) for i in range(30)]
higher = HigherTimeframeTrendStrategy().generate_with_higher_timeframe(trend, htf, ctx)
assert higher
assert all(x.signal_time >= 0 for x in higher)

# Mean-reversion candidate after an extreme completed-bar excursion.
mean_bars = [bar(i, 100 + (0.1 if i % 2 else -0.1)) for i in range(50)]
mean_bars.append(bar(50, 90, spread=1.0))
mr = MeanReversionStrategy(window=30, z_entry=1.5).generate(mean_bars, ctx)
assert mr and mr[-1].direction is Direction.LONG

# Volatility expansion/momentum candidate.
vol_bars = [bar(i, 100 + i * 0.1, spread=0.2) for i in range(30)]
vol_bars.append(MarketBar(30_000, 30_999, 103, 110, 102, 109))
vm = VolatilityMomentumStrategy(expansion_multiple=1.5).generate(vol_bars, ctx)
assert vm and vm[-1].direction is Direction.LONG

# Funding-aware strategy fails closed when funding is unavailable and blocks a
# crowded long when the latest known rate is above the configured threshold.
fa = FundingAwareMomentumStrategy(max_abs_crowded_rate=0.0005)
assert fa.generate(vol_bars, ctx) == []
known_time = vm[-1].signal_time
assert fa.generate_with_funding(vol_bars, ctx, {known_time - 1: 0.001}) == []
allowed = fa.generate_with_funding(vol_bars, ctx, {known_time - 1: 0.0})
assert allowed
assert all(x.strategy_id == "funding_aware_momentum" for x in allowed)

# Pullback/regime implementations must remain deterministic and use completed
# bars; a lack of signal is a valid hypothesis result.
PullbackTrendStrategy().generate(trend, ctx)
RegimeAdaptiveStrategy().generate(trend, ctx)

ranking = cross_sectional_momentum_rank({
    "FASTUSDT": [bar(i, 100 + i * 2) for i in range(25)],
    "SLOWUSDT": [bar(i, 100 + i * 0.2) for i in range(25)],
    "DOWNUSDT": [bar(i, 150 - i) for i in range(25)],
}, lookback=20)
assert [x[0] for x in ranking] == ["FASTUSDT", "SLOWUSDT", "DOWNUSDT"]

print("ALL CHECKS PASSED")
