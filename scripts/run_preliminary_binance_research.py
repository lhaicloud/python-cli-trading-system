"""Run preliminary multi-asset strategy research on real Binance Vision data.

This is deliberately NOT a promotion run. Historical execution-cost evidence is
still incomplete for the 2026 window and shadow duration is zero, so the
mandatory promotion engine must keep every strategy non-live regardless of
headline backtest metrics.

Output:
    research-output/preliminary-research.json
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from statistics import mean

import httpx

from app.research.archive_history import attach_mark_prices, load_monthly_bars, load_monthly_funding
from app.research.backtester import ResearchBacktestConfig, TradeIntent, TradeRecord
from app.research.binance_archive import ArchiveDataset
from app.research.safe_backtester import ResearchBacktester
from app.research.strategies import (
    MeanReversionStrategy,
    PullbackTrendStrategy,
    StrategyContext,
    TrendBreakoutStrategy,
    VolatilityMomentumStrategy,
)
from app.research.validation import ValidationBundle, build_validation_report


@dataclass(frozen=True)
class SymbolProfile:
    symbol: str
    asset_class: str
    start_ym: str
    end_ym: str
    assumed_spread_bps: float
    slippage_bps: float


PROFILES = (
    SymbolProfile("BTCUSDT", "crypto", "2026-03", "2026-08", 5.0, 5.0),
    SymbolProfile("ETHUSDT", "crypto", "2026-03", "2026-08", 5.0, 5.0),
    SymbolProfile("SOLUSDT", "crypto", "2026-03", "2026-08", 7.5, 7.5),
    SymbolProfile("XRPUSDT", "crypto", "2026-03", "2026-08", 7.5, 7.5),
    # Metals are intentionally limited to the post-2026-05-08 commodity index
    # methodology regime instead of pooling incompatible pre/post behavior.
    SymbolProfile("XAUUSDT", "gold", "2026-06", "2026-08", 10.0, 10.0),
    SymbolProfile("XAGUSDT", "silver", "2026-06", "2026-08", 12.5, 12.5),
)

STRATEGIES = (
    TrendBreakoutStrategy(),
    PullbackTrendStrategy(),
    MeanReversionStrategy(),
    VolatilityMomentumStrategy(),
)

INITIAL_CAPITAL = 100_000.0
NOTIONAL_PER_TRADE = 1_000.0


@dataclass(frozen=True)
class LoadedSymbol:
    profile: SymbolProfile
    bars: tuple
    mark_bars: tuple
    funding: tuple
    source_hashes: tuple[str, ...]


def load_symbol(profile: SymbolProfile, client: httpx.Client) -> LoadedSymbol:
    trade = load_monthly_bars(
        symbol=profile.symbol,
        dataset=ArchiveDataset.KLINES,
        interval="1h",
        start_ym=profile.start_ym,
        end_ym=profile.end_ym,
        client=client,
    )
    mark = load_monthly_bars(
        symbol=profile.symbol,
        dataset=ArchiveDataset.MARK_PRICE,
        interval="1h",
        start_ym=profile.start_ym,
        end_ym=profile.end_ym,
        client=client,
    )
    funding_raw = load_monthly_funding(
        symbol=profile.symbol,
        start_ym=profile.start_ym,
        end_ym=profile.end_ym,
        client=client,
    )
    funding = attach_mark_prices(funding_raw.events, mark.bars)
    hashes = trade.source_hashes + mark.source_hashes + funding_raw.source_hashes
    return LoadedSymbol(profile, trade.bars, mark.bars, funding, hashes)


def context_for(loaded: LoadedSymbol) -> StrategyContext:
    reference = loaded.bars[0].close
    return StrategyContext(
        symbol=loaded.profile.symbol,
        asset_class=loaded.profile.asset_class,
        quantity=NOTIONAL_PER_TRADE / reference,
        leverage=1.0,
    )


def base_config(profile: SymbolProfile, *, slippage_multiplier: float = 1.0) -> ResearchBacktestConfig:
    return ResearchBacktestConfig(
        require_book=False,  # provenance gate prevents promotion while this is assumed
        require_funding=True,
        assumed_spread_bps=profile.assumed_spread_bps,
        slippage_bps=profile.slippage_bps * slippage_multiplier,
        max_spread_bps=100.0,
        max_entry_drift_bps=150.0,
        min_net_reward_risk=1.0,
        transaction_cost_multiplier=1.0,
        require_liquidation_check_for_leverage=True,
    )


def delayed(intents: list[TradeIntent], hours: int) -> list[TradeIntent]:
    delta = hours * 3_600_000
    return [replace(intent, intent_id=f"{intent.intent_id}:delay{hours}h", signal_time=intent.signal_time + delta) for intent in intents]


def run_intents(
    loaded: LoadedSymbol,
    intents: list[TradeIntent],
    *,
    slippage_multiplier: float = 1.0,
) -> tuple[TradeRecord, ...]:
    engine = ResearchBacktester(
        trade_bars=loaded.bars,
        funding_events=loaded.funding,
        mark_bars=loaded.mark_bars,
        config=base_config(loaded.profile, slippage_multiplier=slippage_multiplier),
    )
    return engine.run(intents).trades


def neighbor_strategies(strategy):
    if isinstance(strategy, TrendBreakoutStrategy):
        return (replace(strategy, breakout_lookback=15), replace(strategy, breakout_lookback=25))
    if isinstance(strategy, PullbackTrendStrategy):
        return (replace(strategy, pullback_atr=0.4), replace(strategy, pullback_atr=0.6))
    if isinstance(strategy, MeanReversionStrategy):
        return (replace(strategy, z_entry=1.8), replace(strategy, z_entry=2.2))
    if isinstance(strategy, VolatilityMomentumStrategy):
        return (replace(strategy, expansion_multiple=1.5), replace(strategy, expansion_multiple=2.0))
    return ()


def split_records(records: list[TradeRecord]):
    records = sorted(records, key=lambda r: (r.signal_time, r.symbol, r.intent_id))
    n = len(records)
    dev_end = int(n * 0.60)
    val_end = int(n * 0.80)
    return records[:dev_end], records[dev_end:val_end], records[val_end:]


def result_category(status: str, passed: bool) -> str:
    if passed:
        return "PROVEN_BY_CURRENT_EVIDENCE"
    if status == "REJECTED":
        return "REJECTED"
    if status in {"SHADOW", "VALIDATION"}:
        return "STILL_UNVERIFIED"
    return "INSUFFICIENT_EVIDENCE"


def main() -> int:
    out_dir = Path(os.environ.get("RESEARCH_OUTPUT_DIR", "research-output"))
    out_dir.mkdir(parents=True, exist_ok=True)

    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        loaded_symbols = {profile.symbol: load_symbol(profile, client) for profile in PROFILES}

    report: dict = {
        "research_type": "PRELIMINARY_NON_PROMOTABLE",
        "data_source": "Binance Vision official USD-M public archives with published SHA-256 verification",
        "execution_cost_evidence_verified": False,
        "reason_execution_cost_unverified": "2026 research uses explicit conservative assumed spread/slippage rather than contemporaneous complete historical L1 for every symbol",
        "shadow_days": 0,
        "symbols": {},
        "strategies": {},
    }

    for symbol, loaded in loaded_symbols.items():
        report["symbols"][symbol] = {
            "asset_class": loaded.profile.asset_class,
            "start_month": loaded.profile.start_ym,
            "end_month": loaded.profile.end_ym,
            "bars": len(loaded.bars),
            "mark_bars": len(loaded.mark_bars),
            "funding_events": len(loaded.funding),
            "source_sha256_count": len(loaded.source_hashes),
            "structural_break_policy": "post_2026_05_08_only" if loaded.profile.asset_class in {"gold", "silver"} else "single_window_no_known_tradfi_break",
        }

    for strategy in STRATEGIES:
        baseline: list[TradeRecord] = []
        delay_1h: list[TradeRecord] = []
        delay_2h: list[TradeRecord] = []
        slip_15: list[TradeRecord] = []
        slip_20: list[TradeRecord] = []
        intents_by_symbol: dict[str, list[TradeIntent]] = {}

        for symbol, loaded in loaded_symbols.items():
            intents = strategy.generate(loaded.bars, context_for(loaded))
            intents_by_symbol[symbol] = intents
            baseline.extend(run_intents(loaded, intents))
            delay_1h.extend(run_intents(loaded, delayed(intents, 1)))
            delay_2h.extend(run_intents(loaded, delayed(intents, 2)))
            slip_15.extend(run_intents(loaded, intents, slippage_multiplier=1.5))
            slip_20.extend(run_intents(loaded, intents, slippage_multiplier=2.0))

        neighbor_expectancies: list[float] = []
        for neighbor in neighbor_strategies(strategy):
            neighbor_records: list[TradeRecord] = []
            for symbol, loaded in loaded_symbols.items():
                n_intents = neighbor.generate(loaded.bars, context_for(loaded))
                neighbor_records.extend(run_intents(loaded, n_intents))
            neighbor_expectancies.append(mean(r.net_pnl for r in neighbor_records) if neighbor_records else 0.0)

        dev, val, untouched = split_records(baseline)
        validation = build_validation_report(
            ValidationBundle(
                development=dev,
                validation=val,
                untouched_test=untouched,
                entry_delay_variants={"delay_1h": delay_1h, "delay_2h": delay_2h},
                slippage_variants={"slippage_1_5x": slip_15, "slippage_2x": slip_20},
                parameter_neighbor_expectancies=neighbor_expectancies,
                initial_capital=INITIAL_CAPITAL,
                drawdown_budget_pct=15.0,
                shadow_days=0,
                shadow_expectancy=None,
            )
        )

        promotion = validation.promotion
        strategy_payload = {
            "status": promotion.status.value,
            "promotion_passed": promotion.passed,
            "evidence_category": result_category(promotion.status.value, promotion.passed),
            "trades": validation.combined.trades,
            "untouched_trades": validation.untouched_test.trades,
            "expectancy_after_costs": validation.combined.expectancy,
            "profit_factor": validation.combined.profit_factor,
            "win_rate": validation.combined.win_rate,
            "max_drawdown_pct": validation.combined.max_drawdown_pct,
            "cost_stress_1_5x_expectancy": validation.cost_stress_1_5x_expectancy,
            "cost_stress_2x_expectancy": validation.cost_stress_2x_expectancy,
            "untouched_expectancy": validation.untouched_test.expectancy,
            "median_symbol_expectancy": validation.median_symbol_expectancy,
            "profit_concentration": validation.profit_concentration,
            "entry_delay_expectancies": dict(validation.entry_delay_expectancies),
            "slippage_variant_expectancies": dict(validation.slippage_variant_expectancies),
            "parameter_neighbor_expectancies": neighbor_expectancies,
            "symbol_expectancy": dict(validation.symbol_expectancy),
            "asset_class_expectancy": dict(validation.asset_class_expectancy),
            "monte_carlo": asdict(validation.monte_carlo) if validation.monte_carlo else None,
            "failed_gates": [asdict(g) for g in promotion.failed_gates],
            "signals_generated": {symbol: len(items) for symbol, items in intents_by_symbol.items()},
        }
        report["strategies"][strategy.strategy_id] = strategy_payload
        print(
            f"{strategy.strategy_id:24s} status={strategy_payload['status']:13s} "
            f"trades={strategy_payload['trades']:4d} PF={strategy_payload['profit_factor']:.3f} "
            f"exp={strategy_payload['expectancy_after_costs']:.4f} "
            f"OOS={strategy_payload['untouched_trades']:3d}"
        )

    categories = [payload["evidence_category"] for payload in report["strategies"].values()]
    if any(category == "PROVEN_BY_CURRENT_EVIDENCE" for category in categories):
        # This should be impossible while provenance/shadow gates remain false.
        raise RuntimeError("preliminary run unexpectedly promoted a strategy")

    output = out_dir / "preliminary-research.json"
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(f"WROTE {output}")
    print("NO STRATEGY IS AUTHORIZED FOR LIVE TRADING BY THIS RUN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
