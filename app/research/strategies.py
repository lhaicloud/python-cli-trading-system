"""Candidate research strategy families.

These implementations generate hypotheses, not approved trading systems. Every
signal is stamped at a COMPLETED bar close and is intended for execution only on
a later market event by `app.research.safe_backtester.ResearchBacktester`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import mean, pstdev
from typing import Mapping, Protocol, Sequence

from app.research.backtester import Direction, MarketBar, TradeIntent


@dataclass(frozen=True)
class StrategyContext:
    symbol: str
    asset_class: str
    quantity: float
    leverage: float = 1.0
    liquidation_price: float | None = None


class ResearchStrategy(Protocol):
    strategy_id: str
    strategy_version: str

    def generate(self, bars: Sequence[MarketBar], context: StrategyContext) -> list[TradeIntent]: ...


def _sma(values: Sequence[float], window: int) -> float:
    if window <= 0 or len(values) < window:
        raise ValueError("insufficient values for SMA")
    return mean(values[-window:])


def _true_range(current: MarketBar, previous_close: float) -> float:
    return max(
        current.high - current.low,
        abs(current.high - previous_close),
        abs(current.low - previous_close),
    )


def _atr(bars: Sequence[MarketBar], end_idx: int, window: int = 14) -> float:
    if end_idx < window:
        raise ValueError("insufficient bars for ATR")
    ranges = [
        _true_range(bars[i], bars[i - 1].close)
        for i in range(end_idx - window + 1, end_idx + 1)
    ]
    atr = mean(ranges)
    if not math.isfinite(atr) or atr <= 0:
        raise ValueError("invalid ATR")
    return atr


def _intent(
    *,
    strategy_id: str,
    strategy_version: str,
    context: StrategyContext,
    bar: MarketBar,
    direction: Direction,
    atr: float,
    stop_atr: float,
    reward_risk: float,
) -> TradeIntent:
    if context.quantity <= 0:
        raise ValueError("quantity must be positive")
    entry = bar.close
    risk = atr * stop_atr
    if direction is Direction.LONG:
        stop = entry - risk
        target = entry + risk * reward_risk
    else:
        stop = entry + risk
        target = entry - risk * reward_risk
    return TradeIntent(
        intent_id=f"{strategy_id}:{strategy_version}:{context.symbol}:{bar.close_time}",
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        symbol=context.symbol,
        asset_class=context.asset_class,
        direction=direction,
        signal_time=bar.close_time,
        signal_price=entry,
        stop_loss=stop,
        take_profit=target,
        quantity=context.quantity,
        leverage=context.leverage,
        liquidation_price=context.liquidation_price,
    )


@dataclass(frozen=True)
class TrendBreakoutStrategy:
    strategy_id: str = "trend_breakout"
    strategy_version: str = "1"
    breakout_lookback: int = 20
    trend_window: int = 50
    atr_window: int = 14
    stop_atr: float = 1.5
    reward_risk: float = 2.0

    def generate(self, bars: Sequence[MarketBar], context: StrategyContext) -> list[TradeIntent]:
        out: list[TradeIntent] = []
        warmup = max(self.breakout_lookback, self.trend_window, self.atr_window) + 1
        closes = [b.close for b in bars]
        for i in range(warmup, len(bars)):
            bar = bars[i]
            prior = bars[i - self.breakout_lookback:i]
            trend = mean(closes[i - self.trend_window:i])
            atr = _atr(bars, i, self.atr_window)
            if bar.close > max(b.high for b in prior) and bar.close > trend:
                out.append(_intent(strategy_id=self.strategy_id, strategy_version=self.strategy_version,
                                   context=context, bar=bar, direction=Direction.LONG, atr=atr,
                                   stop_atr=self.stop_atr, reward_risk=self.reward_risk))
            elif bar.close < min(b.low for b in prior) and bar.close < trend:
                out.append(_intent(strategy_id=self.strategy_id, strategy_version=self.strategy_version,
                                   context=context, bar=bar, direction=Direction.SHORT, atr=atr,
                                   stop_atr=self.stop_atr, reward_risk=self.reward_risk))
        return out


@dataclass(frozen=True)
class PullbackTrendStrategy:
    strategy_id: str = "trend_pullback"
    strategy_version: str = "1"
    fast_window: int = 20
    slow_window: int = 50
    atr_window: int = 14
    pullback_atr: float = 0.5
    stop_atr: float = 1.5
    reward_risk: float = 2.0

    def generate(self, bars: Sequence[MarketBar], context: StrategyContext) -> list[TradeIntent]:
        out: list[TradeIntent] = []
        warmup = max(self.slow_window, self.atr_window) + 1
        closes = [b.close for b in bars]
        for i in range(warmup, len(bars)):
            bar = bars[i]
            fast = mean(closes[i - self.fast_window:i])
            slow = mean(closes[i - self.slow_window:i])
            atr = _atr(bars, i, self.atr_window)
            near_fast = abs(bar.close - fast) <= self.pullback_atr * atr
            if fast > slow and near_fast and bar.close > bar.open:
                out.append(_intent(strategy_id=self.strategy_id, strategy_version=self.strategy_version,
                                   context=context, bar=bar, direction=Direction.LONG, atr=atr,
                                   stop_atr=self.stop_atr, reward_risk=self.reward_risk))
            elif fast < slow and near_fast and bar.close < bar.open:
                out.append(_intent(strategy_id=self.strategy_id, strategy_version=self.strategy_version,
                                   context=context, bar=bar, direction=Direction.SHORT, atr=atr,
                                   stop_atr=self.stop_atr, reward_risk=self.reward_risk))
        return out


@dataclass(frozen=True)
class MeanReversionStrategy:
    strategy_id: str = "mean_reversion"
    strategy_version: str = "1"
    window: int = 30
    z_entry: float = 2.0
    atr_window: int = 14
    stop_atr: float = 1.25
    reward_risk: float = 1.5

    def generate(self, bars: Sequence[MarketBar], context: StrategyContext) -> list[TradeIntent]:
        out: list[TradeIntent] = []
        warmup = max(self.window, self.atr_window) + 1
        closes = [b.close for b in bars]
        for i in range(warmup, len(bars)):
            sample = closes[i - self.window:i]
            sigma = pstdev(sample)
            if sigma <= 0:
                continue
            z = (bars[i].close - mean(sample)) / sigma
            atr = _atr(bars, i, self.atr_window)
            if z <= -self.z_entry:
                out.append(_intent(strategy_id=self.strategy_id, strategy_version=self.strategy_version,
                                   context=context, bar=bars[i], direction=Direction.LONG, atr=atr,
                                   stop_atr=self.stop_atr, reward_risk=self.reward_risk))
            elif z >= self.z_entry:
                out.append(_intent(strategy_id=self.strategy_id, strategy_version=self.strategy_version,
                                   context=context, bar=bars[i], direction=Direction.SHORT, atr=atr,
                                   stop_atr=self.stop_atr, reward_risk=self.reward_risk))
        return out


@dataclass(frozen=True)
class VolatilityMomentumStrategy:
    strategy_id: str = "volatility_momentum"
    strategy_version: str = "1"
    atr_window: int = 14
    expansion_multiple: float = 1.75
    momentum_lookback: int = 5
    stop_atr: float = 1.5
    reward_risk: float = 2.0

    def generate(self, bars: Sequence[MarketBar], context: StrategyContext) -> list[TradeIntent]:
        out: list[TradeIntent] = []
        warmup = max(self.atr_window + 1, self.momentum_lookback + 1)
        for i in range(warmup, len(bars)):
            atr = _atr(bars, i - 1, self.atr_window)
            bar = bars[i]
            current_range = bar.high - bar.low
            momentum = bar.close - bars[i - self.momentum_lookback].close
            if current_range < atr * self.expansion_multiple:
                continue
            if momentum > 0 and bar.close > bar.open:
                out.append(_intent(strategy_id=self.strategy_id, strategy_version=self.strategy_version,
                                   context=context, bar=bar, direction=Direction.LONG, atr=atr,
                                   stop_atr=self.stop_atr, reward_risk=self.reward_risk))
            elif momentum < 0 and bar.close < bar.open:
                out.append(_intent(strategy_id=self.strategy_id, strategy_version=self.strategy_version,
                                   context=context, bar=bar, direction=Direction.SHORT, atr=atr,
                                   stop_atr=self.stop_atr, reward_risk=self.reward_risk))
        return out


@dataclass(frozen=True)
class FundingAwareMomentumStrategy:
    strategy_id: str = "funding_aware_momentum"
    strategy_version: str = "1"
    max_abs_crowded_rate: float = 0.0005
    base: VolatilityMomentumStrategy = VolatilityMomentumStrategy()

    def generate_with_funding(
        self,
        bars: Sequence[MarketBar],
        context: StrategyContext,
        funding_rate_at_or_before: Mapping[int, float],
    ) -> list[TradeIntent]:
        """Filter momentum signals using only funding known by signal time.

        Mapping keys are funding timestamps. A positive crowded rate blocks new
        longs; a negative crowded rate blocks new shorts.
        """
        candidates = self.base.generate(bars, context)
        timestamps = sorted(funding_rate_at_or_before)
        out: list[TradeIntent] = []
        for candidate in candidates:
            known = [t for t in timestamps if t <= candidate.signal_time]
            if not known:
                continue  # fail closed when funding is required but unavailable
            rate = float(funding_rate_at_or_before[known[-1]])
            crowded = (
                candidate.direction is Direction.LONG and rate > self.max_abs_crowded_rate
            ) or (
                candidate.direction is Direction.SHORT and rate < -self.max_abs_crowded_rate
            )
            if not crowded:
                out.append(TradeIntent(**{**candidate.__dict__,
                    "intent_id": candidate.intent_id.replace(self.base.strategy_id, self.strategy_id, 1),
                    "strategy_id": self.strategy_id,
                    "strategy_version": self.strategy_version,
                }))
        return out

    def generate(self, bars: Sequence[MarketBar], context: StrategyContext) -> list[TradeIntent]:
        # Funding-aware research must supply funding explicitly; no silent zero.
        return []


@dataclass(frozen=True)
class HigherTimeframeTrendStrategy:
    strategy_id: str = "higher_timeframe_trend"
    strategy_version: str = "1"
    htf_window: int = 20
    atr_window: int = 14
    stop_atr: float = 1.5
    reward_risk: float = 2.0

    def generate_with_higher_timeframe(
        self,
        bars: Sequence[MarketBar],
        higher_bars: Sequence[MarketBar],
        context: StrategyContext,
    ) -> list[TradeIntent]:
        """Generate only when a COMPLETED higher-timeframe trend agrees."""
        out: list[TradeIntent] = []
        if len(higher_bars) < self.htf_window:
            return out
        for i in range(self.atr_window + 1, len(bars)):
            bar = bars[i]
            known_htf = [h for h in higher_bars if h.close_time <= bar.close_time]
            if len(known_htf) < self.htf_window:
                continue
            recent_htf = known_htf[-self.htf_window:]
            trend_delta = recent_htf[-1].close - recent_htf[0].close
            atr = _atr(bars, i, self.atr_window)
            if trend_delta > 0 and bar.close > bar.open:
                direction = Direction.LONG
            elif trend_delta < 0 and bar.close < bar.open:
                direction = Direction.SHORT
            else:
                continue
            out.append(_intent(strategy_id=self.strategy_id, strategy_version=self.strategy_version,
                               context=context, bar=bar, direction=direction, atr=atr,
                               stop_atr=self.stop_atr, reward_risk=self.reward_risk))
        return out

    def generate(self, bars: Sequence[MarketBar], context: StrategyContext) -> list[TradeIntent]:
        return []


def cross_sectional_momentum_rank(
    bars_by_symbol: Mapping[str, Sequence[MarketBar]],
    lookback: int = 20,
) -> list[tuple[str, float]]:
    """Rank symbols by completed-bar return; no selection/promotion is implied."""
    if lookback <= 0:
        raise ValueError("lookback must be positive")
    scores: list[tuple[str, float]] = []
    for symbol, bars in bars_by_symbol.items():
        if len(bars) <= lookback or bars[-lookback - 1].close <= 0:
            continue
        score = bars[-1].close / bars[-lookback - 1].close - 1.0
        scores.append((symbol, score))
    return sorted(scores, key=lambda item: item[1], reverse=True)


@dataclass(frozen=True)
class RegimeAdaptiveStrategy:
    """Select one family by observed regime rather than blending predictions."""

    strategy_id: str = "regime_adaptive"
    strategy_version: str = "1"
    fast_window: int = 20
    slow_window: int = 50
    atr_window: int = 14
    trend_strength_atr: float = 1.0

    def generate(self, bars: Sequence[MarketBar], context: StrategyContext) -> list[TradeIntent]:
        if len(bars) < self.slow_window + 2:
            return []
        closes = [b.close for b in bars]
        fast = _sma(closes, self.fast_window)
        slow = _sma(closes, self.slow_window)
        atr = _atr(bars, len(bars) - 1, self.atr_window)
        trend_strength = abs(fast - slow) / atr
        selected: ResearchStrategy
        if trend_strength >= self.trend_strength_atr:
            selected = TrendBreakoutStrategy()
        else:
            selected = MeanReversionStrategy()
        candidates = selected.generate(bars, context)
        return [
            TradeIntent(**{**c.__dict__,
                "intent_id": c.intent_id.replace(c.strategy_id, self.strategy_id, 1),
                "strategy_id": self.strategy_id,
                "strategy_version": self.strategy_version,
            })
            for c in candidates
        ]
