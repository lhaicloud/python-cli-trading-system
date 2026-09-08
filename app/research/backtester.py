"""Research-grade event-driven/equivalent perpetual backtester.

This engine is intentionally separate from the production LQ-MTF engine. It
consumes immutable, pre-generated trade intents so signal generation and
execution remain separate and no candle-close signal can fill before the next
executable market event.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from app.research.costs import ExecutionCosts


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class BacktestDataError(RuntimeError):
    pass


@dataclass(frozen=True)
class MarketBar:
    open_time: int
    close_time: int
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class BookQuote:
    timestamp: int
    bid: float
    ask: float
    bid_qty: float = 0.0
    ask_qty: float = 0.0


@dataclass(frozen=True)
class FundingEvent:
    timestamp: int
    rate: float
    mark_price: float | None = None


@dataclass(frozen=True)
class TradeIntent:
    intent_id: str
    strategy_id: str
    strategy_version: str
    symbol: str
    asset_class: str
    direction: Direction
    signal_time: int
    signal_price: float
    stop_loss: float
    take_profit: float
    quantity: float
    leverage: float = 1.0
    liquidation_price: float | None = None
    max_hold_bars: int = 96


@dataclass(frozen=True)
class ResearchBacktestConfig:
    taker_fee_rate: float = 0.0005
    maker_fee_rate: float = 0.0002
    slippage_bps: float = 5.0
    assumed_spread_bps: float = 5.0
    max_spread_bps: float = 30.0
    max_entry_drift_bps: float = 50.0
    min_net_reward_risk: float = 1.2
    transaction_cost_multiplier: float = 1.0
    require_book: bool = True
    quote_freshness_ms: int = 60_000
    require_funding: bool = True
    expected_funding_interval_ms: int | None = None
    require_liquidation_check_for_leverage: bool = True


@dataclass(frozen=True)
class TradeRecord:
    intent_id: str
    strategy_id: str
    strategy_version: str
    symbol: str
    asset_class: str
    direction: Direction
    signal_time: int
    signal_price: float
    entry_time: int
    entry_price: float
    exit_time: int
    exit_price: float
    exit_reason: str
    quantity: float
    gross_mid_pnl: float
    fees: float
    spread_cost: float
    slippage_cost: float
    funding_cost: float
    net_pnl: float


@dataclass(frozen=True)
class Rejection:
    intent_id: str
    reason: str


@dataclass(frozen=True)
class ResearchBacktestResult:
    trades: tuple[TradeRecord, ...]
    rejections: tuple[Rejection, ...]
    expectancy: float
    profit_factor: float


class ResearchBacktester:
    def __init__(
        self,
        *,
        trade_bars: Sequence[MarketBar],
        book_quotes: Sequence[BookQuote] = (),
        funding_events: Sequence[FundingEvent] = (),
        mark_bars: Sequence[MarketBar] = (),
        config: ResearchBacktestConfig | None = None,
    ) -> None:
        self.config = config or ResearchBacktestConfig()
        self.trade_bars = tuple(sorted(trade_bars, key=lambda b: b.open_time))
        self.book_quotes = tuple(sorted(book_quotes, key=lambda q: q.timestamp))
        self.funding_events = tuple(sorted(funding_events, key=lambda f: f.timestamp))
        self.mark_bars = {b.open_time: b for b in mark_bars}
        self._bar_opens = [b.open_time for b in self.trade_bars]
        self._quote_times = [q.timestamp for q in self.book_quotes]
        self._validate_market_data()

    def _validate_market_data(self) -> None:
        if not self.trade_bars:
            raise BacktestDataError("trade bars are required")
        previous_close = -1
        for bar in self.trade_bars:
            if bar.open_time <= previous_close:
                raise BacktestDataError("trade bars overlap or are not strictly chronological")
            if bar.close_time < bar.open_time or min(bar.open, bar.high, bar.low, bar.close) <= 0:
                raise BacktestDataError("invalid trade bar")
            if bar.high < max(bar.open, bar.close) or bar.low > min(bar.open, bar.close):
                raise BacktestDataError("invalid OHLC geometry")
            previous_close = bar.close_time

        if self.config.require_funding:
            span = self.trade_bars[-1].close_time - self.trade_bars[0].open_time
            interval = self.config.expected_funding_interval_ms
            if span > 0 and not self.funding_events and (interval is None or span >= interval):
                raise BacktestDataError("funding history is required but missing")
            if interval and self.funding_events:
                times = [f.timestamp for f in self.funding_events]
                if any(b - a > int(interval * 1.5) for a, b in zip(times, times[1:])):
                    raise BacktestDataError("funding history contains an excessive gap")

    def run(self, intents: Sequence[TradeIntent]) -> ResearchBacktestResult:
        trades: list[TradeRecord] = []
        rejections: list[Rejection] = []
        seen_intents: set[str] = set()
        occupied_until = -1

        for intent in sorted(intents, key=lambda x: x.signal_time):
            reason = self._validate_intent(intent, seen_intents)
            if reason:
                rejections.append(Rejection(intent.intent_id, reason))
                continue
            seen_intents.add(intent.intent_id)

            entry_idx = bisect_right(self._bar_opens, intent.signal_time)
            if entry_idx >= len(self.trade_bars):
                rejections.append(Rejection(intent.intent_id, "no_next_executable_bar"))
                continue
            entry_bar = self.trade_bars[entry_idx]
            if entry_bar.open_time <= occupied_until:
                rejections.append(Rejection(intent.intent_id, "overlapping_position"))
                continue

            try:
                record = self._simulate(intent, entry_idx)
            except BacktestDataError as exc:
                rejections.append(Rejection(intent.intent_id, f"data_error:{exc}"))
                continue
            if isinstance(record, Rejection):
                rejections.append(record)
                continue

            trades.append(record)
            occupied_until = record.exit_time

        pnls = [t.net_pnl for t in trades]
        expectancy = sum(pnls) / len(pnls) if pnls else 0.0
        gross_profit = sum(p for p in pnls if p > 0)
        gross_loss = abs(sum(p for p in pnls if p <= 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)
        return ResearchBacktestResult(
            trades=tuple(trades),
            rejections=tuple(rejections),
            expectancy=expectancy,
            profit_factor=profit_factor,
        )

    def _validate_intent(self, intent: TradeIntent, seen: set[str]) -> str | None:
        if not intent.intent_id or intent.intent_id in seen:
            return "duplicate_or_missing_intent_id"
        if not intent.strategy_id or not intent.strategy_version:
            return "missing_strategy_identity"
        if intent.asset_class.lower() in {"", "unknown"}:
            return "unknown_asset_class"
        if intent.signal_price <= 0 or intent.quantity <= 0:
            return "invalid_signal_price_or_quantity"
        if intent.leverage <= 0:
            return "invalid_leverage"
        if intent.leverage > 1 and self.config.require_liquidation_check_for_leverage and intent.liquidation_price is None:
            return "missing_liquidation_price"
        if intent.max_hold_bars <= 0:
            return "invalid_max_hold_bars"
        if intent.direction is Direction.LONG:
            if not (intent.stop_loss < intent.signal_price < intent.take_profit):
                return "invalid_long_geometry"
        else:
            if not (intent.take_profit < intent.signal_price < intent.stop_loss):
                return "invalid_short_geometry"
        return None

    def _quote_for(self, timestamp: int, fallback_mid: float) -> BookQuote:
        idx = bisect_left(self._quote_times, timestamp)
        if idx < len(self.book_quotes):
            quote = self.book_quotes[idx]
            if quote.timestamp - timestamp <= self.config.quote_freshness_ms:
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

    @staticmethod
    def _mid(quote: BookQuote) -> float:
        return (quote.bid + quote.ask) / 2.0

    def _market_fill(
        self,
        *,
        timestamp: int,
        reference_mid: float,
        side: str,
        quantity: float,
    ) -> tuple[float, float, float, float, float]:
        quote = self._quote_for(timestamp, reference_mid)
        mid = self._mid(quote)
        spread_bps = (quote.ask - quote.bid) / mid * 10_000.0
        if spread_bps > self.config.max_spread_bps:
            raise BacktestDataError(f"spread_too_wide:{spread_bps:.4f}bps")

        slip = self.config.slippage_bps / 10_000.0
        if side == "BUY":
            quote_fill = quote.ask
            actual = quote_fill * (1 + slip)
            spread_cost = max(0.0, quote.ask - mid) * quantity
            slippage_cost = max(0.0, actual - quote_fill) * quantity
        else:
            quote_fill = quote.bid
            actual = quote_fill * (1 - slip)
            spread_cost = max(0.0, mid - quote.bid) * quantity
            slippage_cost = max(0.0, quote_fill - actual) * quantity
        return mid, actual, spread_cost, slippage_cost, spread_bps

    def _simulate(self, intent: TradeIntent, entry_idx: int) -> TradeRecord | Rejection:
        cfg = self.config
        qty = intent.quantity
        entry_bar = self.trade_bars[entry_idx]
        entry_side = "BUY" if intent.direction is Direction.LONG else "SELL"
        try:
            entry_mid, entry_fill, entry_spread, entry_slip, spread_bps = self._market_fill(
                timestamp=entry_bar.open_time,
                reference_mid=entry_bar.open,
                side=entry_side,
                quantity=qty,
            )
        except BacktestDataError as exc:
            return Rejection(intent.intent_id, f"entry_{exc}")

        drift_bps = abs(entry_fill - intent.signal_price) / intent.signal_price * 10_000.0
        if drift_bps > cfg.max_entry_drift_bps:
            return Rejection(intent.intent_id, f"entry_drift:{drift_bps:.4f}bps")

        if intent.direction is Direction.LONG:
            if not (intent.stop_loss < entry_fill < intent.take_profit):
                return Rejection(intent.intent_id, "entry_drift_invalidated_geometry")
            risk_per_unit = entry_fill - intent.stop_loss
            reward_per_unit = intent.take_profit - entry_fill
        else:
            if not (intent.take_profit < entry_fill < intent.stop_loss):
                return Rejection(intent.intent_id, "entry_drift_invalidated_geometry")
            risk_per_unit = intent.stop_loss - entry_fill
            reward_per_unit = entry_fill - intent.take_profit

        multiplier = cfg.transaction_cost_multiplier
        entry_fee = entry_fill * qty * cfg.taker_fee_rate * multiplier
        entry_transaction_cost = (entry_spread + entry_slip) * multiplier + entry_fee
        stop_market_cost_est = (
            intent.stop_loss
            * qty
            * (
                cfg.taker_fee_rate
                + spread_bps / 20_000.0
                + cfg.slippage_bps / 10_000.0
            )
            * multiplier
        )
        tp_fee_est = intent.take_profit * qty * cfg.maker_fee_rate * multiplier
        net_reward = reward_per_unit * qty - entry_transaction_cost - tp_fee_est
        net_risk = risk_per_unit * qty + entry_transaction_cost + stop_market_cost_est
        if net_risk <= 0 or net_reward / net_risk < cfg.min_net_reward_risk:
            return Rejection(intent.intent_id, "insufficient_net_reward_risk")

        funding_cost = 0.0
        entry_time = entry_bar.open_time
        funding_idx = bisect_left([f.timestamp for f in self.funding_events], entry_time)

        for bars_held, bar_idx in enumerate(range(entry_idx, len(self.trade_bars)), start=1):
            bar = self.trade_bars[bar_idx]

            while funding_idx < len(self.funding_events):
                event = self.funding_events[funding_idx]
                if event.timestamp > bar.close_time:
                    break
                if event.timestamp >= entry_time:
                    mark = event.mark_price or bar.close
                    notional = abs(mark * qty)
                    funding_cost += notional * event.rate * (1 if intent.direction is Direction.LONG else -1)
                funding_idx += 1

            if intent.liquidation_price is not None:
                mark_bar = self.mark_bars.get(bar.open_time)
                if mark_bar is None and cfg.require_liquidation_check_for_leverage and intent.leverage > 1:
                    raise BacktestDataError("missing_mark_bar_for_liquidation_check")
                if mark_bar is not None:
                    liquidated = (
                        mark_bar.low <= intent.liquidation_price
                        if intent.direction is Direction.LONG
                        else mark_bar.high >= intent.liquidation_price
                    )
                    if liquidated:
                        return self._close_market(
                            intent=intent,
                            entry_mid=entry_mid,
                            entry_fill=entry_fill,
                            entry_spread=entry_spread,
                            entry_slip=entry_slip,
                            entry_fee=entry_fee,
                            exit_mid=intent.liquidation_price,
                            exit_time=bar.close_time,
                            exit_reason="liquidation",
                            funding_cost=funding_cost,
                        )

            if intent.direction is Direction.LONG:
                hit_stop = bar.low <= intent.stop_loss
                hit_target = bar.high >= intent.take_profit
            else:
                hit_stop = bar.high >= intent.stop_loss
                hit_target = bar.low <= intent.take_profit

            # Conservative ambiguous-candle rule: stop wins whenever both are
            # reachable and intrabar ordering is unavailable.
            if hit_stop:
                return self._close_market(
                    intent=intent,
                    entry_mid=entry_mid,
                    entry_fill=entry_fill,
                    entry_spread=entry_spread,
                    entry_slip=entry_slip,
                    entry_fee=entry_fee,
                    exit_mid=intent.stop_loss,
                    exit_time=bar.close_time,
                    exit_reason="stop_loss",
                    funding_cost=funding_cost,
                )
            if hit_target:
                return self._close_limit_target(
                    intent=intent,
                    entry_mid=entry_mid,
                    entry_fill=entry_fill,
                    entry_spread=entry_spread,
                    entry_slip=entry_slip,
                    entry_fee=entry_fee,
                    exit_time=bar.close_time,
                    funding_cost=funding_cost,
                )
            if bars_held >= intent.max_hold_bars:
                return self._close_market(
                    intent=intent,
                    entry_mid=entry_mid,
                    entry_fill=entry_fill,
                    entry_spread=entry_spread,
                    entry_slip=entry_slip,
                    entry_fee=entry_fee,
                    exit_mid=bar.close,
                    exit_time=bar.close_time,
                    exit_reason="time_exit",
                    funding_cost=funding_cost,
                )

        last = self.trade_bars[-1]
        return self._close_market(
            intent=intent,
            entry_mid=entry_mid,
            entry_fill=entry_fill,
            entry_spread=entry_spread,
            entry_slip=entry_slip,
            entry_fee=entry_fee,
            exit_mid=last.close,
            exit_time=last.close_time,
            exit_reason="end_of_data",
            funding_cost=funding_cost,
        )

    def _close_market(
        self,
        *,
        intent: TradeIntent,
        entry_mid: float,
        entry_fill: float,
        entry_spread: float,
        entry_slip: float,
        entry_fee: float,
        exit_mid: float,
        exit_time: int,
        exit_reason: str,
        funding_cost: float,
    ) -> TradeRecord:
        exit_side = "SELL" if intent.direction is Direction.LONG else "BUY"
        exit_reference_mid, exit_fill, exit_spread, exit_slip, _ = self._market_fill(
            timestamp=exit_time,
            reference_mid=exit_mid,
            side=exit_side,
            quantity=intent.quantity,
        )
        return self._build_record(
            intent=intent,
            entry_mid=entry_mid,
            entry_fill=entry_fill,
            entry_spread=entry_spread,
            entry_slip=entry_slip,
            entry_fee=entry_fee,
            exit_mid=exit_reference_mid,
            exit_fill=exit_fill,
            exit_spread=exit_spread,
            exit_slip=exit_slip,
            exit_fee=exit_fill * intent.quantity * self.config.taker_fee_rate * self.config.transaction_cost_multiplier,
            exit_time=exit_time,
            exit_reason=exit_reason,
            funding_cost=funding_cost,
        )

    def _close_limit_target(
        self,
        *,
        intent: TradeIntent,
        entry_mid: float,
        entry_fill: float,
        entry_spread: float,
        entry_slip: float,
        entry_fee: float,
        exit_time: int,
        funding_cost: float,
    ) -> TradeRecord:
        exit_price = intent.take_profit
        return self._build_record(
            intent=intent,
            entry_mid=entry_mid,
            entry_fill=entry_fill,
            entry_spread=entry_spread,
            entry_slip=entry_slip,
            entry_fee=entry_fee,
            exit_mid=exit_price,
            exit_fill=exit_price,
            exit_spread=0.0,
            exit_slip=0.0,
            exit_fee=exit_price * intent.quantity * self.config.maker_fee_rate * self.config.transaction_cost_multiplier,
            exit_time=exit_time,
            exit_reason="take_profit",
            funding_cost=funding_cost,
        )

    def _build_record(
        self,
        *,
        intent: TradeIntent,
        entry_mid: float,
        entry_fill: float,
        entry_spread: float,
        entry_slip: float,
        entry_fee: float,
        exit_mid: float,
        exit_fill: float,
        exit_spread: float,
        exit_slip: float,
        exit_fee: float,
        exit_time: int,
        exit_reason: str,
        funding_cost: float,
    ) -> TradeRecord:
        qty = intent.quantity
        gross_mid_pnl = (
            (exit_mid - entry_mid) * qty
            if intent.direction is Direction.LONG
            else (entry_mid - exit_mid) * qty
        )
        multiplier = self.config.transaction_cost_multiplier
        costs = ExecutionCosts(
            fees=entry_fee + exit_fee,
            spread=(entry_spread + exit_spread) * multiplier,
            slippage=(entry_slip + exit_slip) * multiplier,
            funding=funding_cost,
        )
        return TradeRecord(
            intent_id=intent.intent_id,
            strategy_id=intent.strategy_id,
            strategy_version=intent.strategy_version,
            symbol=intent.symbol,
            asset_class=intent.asset_class,
            direction=intent.direction,
            signal_time=intent.signal_time,
            signal_price=intent.signal_price,
            entry_time=self.trade_bars[bisect_right(self._bar_opens, intent.signal_time)].open_time,
            entry_price=entry_fill,
            exit_time=exit_time,
            exit_price=exit_fill,
            exit_reason=exit_reason,
            quantity=qty,
            gross_mid_pnl=gross_mid_pnl,
            fees=costs.fees,
            spread_cost=costs.spread,
            slippage_cost=costs.slippage,
            funding_cost=costs.funding,
            net_pnl=gross_mid_pnl - costs.total,
        )
