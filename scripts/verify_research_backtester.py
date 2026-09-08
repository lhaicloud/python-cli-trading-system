"""Synthetic, no-network checks for the strict research backtester."""

from __future__ import annotations

from app.research.backtester import (
    BookQuote,
    Direction,
    FundingEvent,
    MarketBar,
    ResearchBacktestConfig,
    ResearchBacktester,
    TradeIntent,
)

failures = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global failures
    status = "PASS" if condition else "FAIL"
    if not condition:
        failures += 1
    print(f"  [{status}] {name}{(' — ' + detail) if detail else ''}")


def bar(t: int, o: float, h: float, l: float, c: float) -> MarketBar:
    return MarketBar(t, t + 999, o, h, l, c)


def config(**overrides) -> ResearchBacktestConfig:
    values = dict(
        require_book=False,
        require_funding=False,
        slippage_bps=0.0,
        assumed_spread_bps=0.0,
        max_entry_drift_bps=100.0,
        min_net_reward_risk=0.5,
    )
    values.update(overrides)
    return ResearchBacktestConfig(**values)


def intent(intent_id: str, **overrides) -> TradeIntent:
    values = dict(
        intent_id=intent_id,
        strategy_id="synthetic",
        strategy_version="1",
        symbol="BTCUSDT",
        asset_class="crypto",
        direction=Direction.LONG,
        signal_time=999,
        signal_price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        quantity=1.0,
    )
    values.update(overrides)
    return TradeIntent(**values)


print("\n1. Next-event execution + conservative same-candle rule")
bars = [
    bar(0, 100, 101, 99, 100),
    bar(1000, 100, 111, 94, 100),
    bar(2000, 100, 105, 98, 103),
]
engine = ResearchBacktester(trade_bars=bars, config=config(min_net_reward_risk=1.0))
result = engine.run([intent("base")])
check("one trade executed", len(result.trades) == 1)
if result.trades:
    trade = result.trades[0]
    check("signal executes on next bar", trade.entry_time == 1000, f"entry_time={trade.entry_time}")
    check("stop wins when stop+target both touched", trade.exit_reason == "stop_loss", trade.exit_reason)
    check("ambiguous candle is losing", trade.net_pnl < 0, f"net_pnl={trade.net_pnl}")

print("\n2. Integrity rejections")
dup = engine.run([intent("dup"), intent("dup")])
check("duplicate intent rejected", any(r.reason == "duplicate_or_missing_intent_id" for r in dup.rejections))

drift = ResearchBacktester(trade_bars=bars, config=config(max_entry_drift_bps=50)).run([
    intent("drift", signal_price=90, stop_loss=80, take_profit=120)
])
check("material entry drift rejected", any(r.reason.startswith("entry_drift:") for r in drift.rejections))

leveraged = engine.run([intent("lev", leverage=2)])
check("leverage without liquidation price rejected", any(r.reason == "missing_liquidation_price" for r in leveraged.rejections))

unknown = engine.run([intent("unknown", symbol="NEWUSDT", asset_class="unknown")])
check("unknown asset class rejected", any(r.reason == "unknown_asset_class" for r in unknown.rejections))

print("\n3. Strict book gates")
strict_missing = ResearchBacktester(
    trade_bars=bars,
    config=config(require_book=True, quote_freshness_ms=100),
).run([intent("book-missing")])
check("missing book rejects entry", any("missing_or_stale_book_quote" in r.reason for r in strict_missing.rejections))

wide_quotes = [BookQuote(1000, bid=99, ask=101), BookQuote(1999, bid=99, ask=101)]
wide = ResearchBacktester(
    trade_bars=bars,
    book_quotes=wide_quotes,
    config=config(require_book=True, max_spread_bps=30, quote_freshness_ms=1000),
).run([intent("wide")])
check("abnormally wide spread rejects", any("spread_too_wide" in r.reason for r in wide.rejections))

print("\n4. Mark-price liquidation")
liq_trade_bars = [
    bar(0, 100, 101, 99, 100),
    bar(1000, 100, 104, 96, 101),
    bar(2000, 101, 103, 99, 102),
]
mark_bars = [
    bar(0, 100, 101, 99, 100),
    bar(1000, 100, 102, 89, 100),
    bar(2000, 100, 102, 99, 100),
]
liq = ResearchBacktester(
    trade_bars=liq_trade_bars,
    mark_bars=mark_bars,
    config=config(),
).run([intent("liq", stop_loss=85, take_profit=115, leverage=2, liquidation_price=90)])
check("mark-price crossing triggers liquidation", len(liq.trades) == 1 and liq.trades[0].exit_reason == "liquidation")

missing_mark = ResearchBacktester(
    trade_bars=liq_trade_bars,
    config=config(),
).run([intent("missing-mark", stop_loss=85, take_profit=115, leverage=2, liquidation_price=90)])
check("leveraged simulation fails closed without mark bars", any("missing_mark_bar" in r.reason for r in missing_mark.rejections))

print("\n5. Funding cash flows")
funding_bars = [
    bar(0, 100, 101, 99, 100),
    bar(1000, 100, 105, 99, 104),
    bar(2000, 104, 121, 103, 120),
]
funding_intent = intent("funding", stop_loss=90, take_profit=120)
control = ResearchBacktester(trade_bars=funding_bars, config=config()).run([funding_intent])
funded = ResearchBacktester(
    trade_bars=funding_bars,
    funding_events=[FundingEvent(timestamp=1500, rate=0.01, mark_price=102)],
    config=config(require_funding=True),
).run([funding_intent])
check("control funding test executes", len(control.trades) == 1)
check("funded test executes", len(funded.trades) == 1)
if control.trades and funded.trades:
    check("positive funding costs a long", funded.trades[0].funding_cost > 0)
    check("funding reduces net PnL", funded.trades[0].net_pnl < control.trades[0].net_pnl)

print("\n6. Full transaction-cost stress")
stress_bars = [
    bar(0, 100, 101, 99, 100),
    bar(1000, 100, 105, 99, 104),
    bar(2000, 104, 121, 103, 120),
]
stress_intent = intent("stress", stop_loss=90, take_profit=120)
normal = ResearchBacktester(
    trade_bars=stress_bars,
    config=config(slippage_bps=5, assumed_spread_bps=5, transaction_cost_multiplier=1.0),
).run([stress_intent])
stressed = ResearchBacktester(
    trade_bars=stress_bars,
    config=config(slippage_bps=5, assumed_spread_bps=5, transaction_cost_multiplier=1.5),
).run([stress_intent])
check("normal cost trade executes", len(normal.trades) == 1)
check("1.5x cost trade executes", len(stressed.trades) == 1)
if normal.trades and stressed.trades:
    base_trade = normal.trades[0]
    stressed_trade = stressed.trades[0]
    check("1.5x costs reduce net PnL", stressed_trade.net_pnl < base_trade.net_pnl)
    check("spread reported separately", base_trade.spread_cost > 0)
    check("slippage reported separately", base_trade.slippage_cost > 0)
    check("fees reported separately", base_trade.fees > 0)

print(f"\n{'ALL CHECKS PASSED' if failures == 0 else f'{failures} CHECK(S) FAILED'}")
raise SystemExit(1 if failures else 0)
