"""Synthetic, no-network checks for the research event backtester.

Run:
    python -m scripts.verify_research_backtester
"""

from __future__ import annotations

from app.research.backtester import (
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


def bar(open_time: int, open_: float, high: float, low: float, close: float) -> MarketBar:
    return MarketBar(open_time, open_time + 999, open_, high, low, close)


bars = [
    bar(0, 100, 101, 99, 100),
    bar(1000, 100, 111, 94, 100),
    bar(2000, 100, 105, 98, 103),
    bar(3000, 103, 111, 102, 110),
]
base_cfg = ResearchBacktestConfig(
    require_book=False,
    require_funding=False,
    slippage_bps=0,
    assumed_spread_bps=0,
    max_entry_drift_bps=100,
    min_net_reward_risk=1.0,
)

print("\n1. Next-event execution + conservative same-candle rule")
engine = ResearchBacktester(trade_bars=bars, config=base_cfg)
intent = TradeIntent(
    intent_id="intent-1",
    strategy_id="synthetic",
    strategy_version="1",
    symbol="BTCUSDT",
    asset_class="crypto",
    direction=Direction.LONG,
    signal_time=999,
    signal_price=100,
    stop_loss=95,
    take_profit=110,
    quantity=1,
)
result = engine.run([intent])
check("one trade executed", len(result.trades) == 1)
trade = result.trades[0]
check("signal executes on next bar", trade.entry_time == 1000, f"entry_time={trade.entry_time}")
check("stop wins when stop+target are both touched", trade.exit_reason == "stop_loss", trade.exit_reason)
check("ambiguous candle is losing, not optimistic", trade.net_pnl < 0, f"net_pnl={trade.net_pnl}")

print("\n2. Intent and entry-integrity rejection")
dup = engine.run([intent, intent])
check("duplicate intent rejected", any(r.reason == "duplicate_or_missing_intent_id" for r in dup.rejections))

drift_intent = TradeIntent(
    intent_id="drift",
    strategy_id="synthetic",
    strategy_version="1",
    symbol="BTCUSDT",
    asset_class="crypto",
    direction=Direction.LONG,
    signal_time=999,
    signal_price=90,
    stop_loss=80,
    take_profit=120,
    quantity=1,
)
drift_result = ResearchBacktester(
    trade_bars=bars,
    config=ResearchBacktestConfig(
        require_book=False,
        require_funding=False,
        slippage_bps=0,
        assumed_spread_bps=0,
        max_entry_drift_bps=50,
        min_net_reward_risk=1.0,
    ),
).run([drift_intent])
check("material entry drift rejected", any(r.reason.startswith("entry_drift:") for r in drift_result.rejections))

leveraged_missing = TradeIntent(
    intent_id="lev-missing",
    strategy_id="synthetic",
    strategy_version="1",
    symbol="BTCUSDT",
    asset_class="crypto",
    direction=Direction.LONG,
    signal_time=999,
    signal_price=100,
    stop_loss=95,
    take_profit=110,
    quantity=1,
    leverage=2,
)
lev_result = engine.run([leveraged_missing])
check("leveraged intent without liquidation price rejected", any(r.reason == "missing_liquidation_price" for r in lev_result.rejections))

unknown = TradeIntent(
    intent_id="unknown-asset",
    strategy_id="synthetic",
    strategy_version="1",
    symbol="NEWUSDT",
    asset_class="unknown",
    direction=Direction.LONG,
    signal_time=999,
    signal_price=100,
    stop_loss=95,
    take_profit=110,
    quantity=1,
)
unknown_result = engine.run([unknown])
check("unknown asset class rejected", any(r.reason == "unknown_asset_class" for r in unknown_result.rejections))

print("\n3. Mark-price liquidation check")
liq_bars = [
    bar(0, 100, 101, 99, 100),
    bar(1000, 100, 104, 96, 101),
    bar(2000, 101, 103, 99, 102),
]
mark_bars = [
    bar(0, 100, 101, 99, 100),
    bar(1000, 100, 102, 89, 100),
    bar(2000, 100, 102, 99, 100),
]
liq_intent = TradeIntent(
    intent_id="liq",
    strategy_id="synthetic",
    strategy_version="1",
    symbol="BTCUSDT",
    asset_class="crypto",
    direction=Direction.LONG,
    signal_time=999,
    signal_price=100,
    stop_loss=95,
    take_profit=115,
    quantity=1,
    leverage=2,
    liquidation_price=90,
)
liq_result = ResearchBacktester(
    trade_bars=liq_bars,
    mark_bars=mark_bars,
    config=base_cfg,
).run([liq_intent])
check("mark-price crossing triggers liquidation", len(liq_result.trades) == 1 and liq_result.trades[0].exit_reason == "liquidation")

print("\n4. Funding cash flows")
funding_bars = [
    bar(0, 100, 101, 99, 100),
    bar(1000, 100, 105, 99, 104),
    bar(2000, 104, 111, 103, 110),
]
funding_intent = TradeIntent(
    intent_id="funding",
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
no_funding = ResearchBacktester(
    trade_bars=funding_bars,
    config=base_cfg,
).run([funding_intent]).trades[0]
with_funding = ResearchBacktester(
    trade_bars=funding_bars,
    funding_events=[FundingEvent(timestamp=1500, rate=0.01, mark_price=102)],
    config=ResearchBacktestConfig(
        require_book=False,
        require_funding=True,
        slippage_bps=0,
        assumed_spread_bps=0,
        max_entry_drift_bps=100,
        min_net_reward_risk=0.5,
    ),
).run([funding_intent]).trades[0]
check("positive funding costs long money", with_funding.funding_cost > 0)
check("funding reduces net PnL", with_funding.net_pnl < no_funding.net_pnl)

print("\n5. Cost stress")
stress_bars = [bar(0, 100, 101, 99, 100), bar(1000, 100, 105, 99, 104), bar(2000, 104, 111, 103, 110)]
stress_intent = TradeIntent(
    intent_id="stress",
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
base_trade = ResearchBacktester(
    trade_bars=stress_bars,
    config=ResearchBacktestConfig(
        require_book=False,
        require_funding=False,
        slippage_bps=5,
        assumed_spread_bps=5,
        min_net_reward_risk=0.5,
        max_entry_drift_bps=100,
        transaction_cost_multiplier=1.0,
    ),
).run([stress_intent]).trades[0]
stressed_trade = ResearchBacktester(
    trade_bars=stress_bars,
    config=ResearchBacktestConfig(
        require_book=False,
        require_funding=False,
        slippage_bps=5,
        assumed_spread_bps=5,
        min_net_reward_risk=0.5,
        max_entry_drift_bps=100,
        transaction_cost_multiplier=1.5,
    ),
).run([stress_intent]).trades[0]
check("1.5x transaction costs lower net PnL", stressed_trade.net_pnl < base_trade.net_pnl)
check("spread is reported separately", base_trade.spread_cost > 0)
check("slippage is reported separately", base_trade.slippage_cost > 0)
check("fees are reported separately", base_trade.fees > 0)

print(f"\n{'ALL CHECKS PASSED' if failures == 0 else f'{failures} CHECK(S) FAILED'}")
raise SystemExit(1 if failures else 0)
