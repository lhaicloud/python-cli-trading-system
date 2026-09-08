"""Explicit per-asset-class research behavior and cost assumptions.

Values in this module are configuration assumptions, not proof of executable
costs. The `execution_cost_evidence_verified` flag remains false until measured
historical/live bid-ask evidence supports the profile.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.data.instruments import AssetClass


@dataclass(frozen=True)
class AssetClassModel:
    asset_class: AssetClass
    taker_fee_rate: float | None
    maker_fee_rate: float | None
    assumed_spread_bps: float | None
    assumed_slippage_bps: float | None
    expected_funding_interval_hours: int | None
    trades_24_7: bool
    weekend_liquidity_risk: bool
    maintenance_regime_sensitive: bool
    max_research_leverage: float
    execution_cost_evidence_verified: bool
    research_enabled: bool
    notes: str

    def require_usable(self) -> "AssetClassModel":
        if not self.research_enabled:
            raise ValueError(f"{self.asset_class.value}: asset-class research model is disabled")
        required = (
            self.taker_fee_rate,
            self.maker_fee_rate,
            self.assumed_spread_bps,
            self.assumed_slippage_bps,
            self.expected_funding_interval_hours,
        )
        if any(value is None for value in required):
            raise ValueError(f"{self.asset_class.value}: required asset-class assumptions are missing")
        if self.max_research_leverage <= 0:
            raise ValueError(f"{self.asset_class.value}: invalid leverage limit")
        return self


# These are intentionally conservative *assumptions* for preliminary research.
# They do not set execution_cost_evidence_verified=True.
_MODELS: dict[AssetClass, AssetClassModel] = {
    AssetClass.CRYPTO: AssetClassModel(
        AssetClass.CRYPTO, 0.0005, 0.0002, 7.5, 7.5, 8,
        True, True, False, 3.0, False, True,
        "Generic liquid USD-M crypto research profile; replace with symbol-specific measured costs.",
    ),
    AssetClass.GOLD: AssetClassModel(
        AssetClass.GOLD, 0.0005, 0.0002, 10.0, 10.0, 8,
        True, True, True, 2.0, False, True,
        "XAU TradFi perpetual; segment known Binance index/funding methodology changes.",
    ),
    AssetClass.SILVER: AssetClassModel(
        AssetClass.SILVER, 0.0005, 0.0002, 12.5, 12.5, 8,
        True, True, True, 2.0, False, True,
        "XAG TradFi perpetual; segment known Binance index/funding methodology changes.",
    ),
    # Other TradFi classes must not inherit crypto assumptions. They are disabled
    # until explicit symbol/class-specific behavior and costs are configured.
    AssetClass.COMMODITY: AssetClassModel(
        AssetClass.COMMODITY, None, None, None, None, None,
        True, True, True, 1.0, False, False,
        "Disabled until commodity-specific costs/funding/specification evidence is configured.",
    ),
    AssetClass.EQUITY: AssetClassModel(
        AssetClass.EQUITY, None, None, None, None, None,
        True, True, True, 1.0, False, False,
        "Disabled until equity-perpetual market-hours/index behavior is explicitly modeled.",
    ),
    AssetClass.INDEX: AssetClassModel(
        AssetClass.INDEX, None, None, None, None, None,
        True, True, True, 1.0, False, False,
        "Disabled until index-perpetual costs and index methodology are explicitly modeled.",
    ),
    AssetClass.FOREX_LIKE: AssetClassModel(
        AssetClass.FOREX_LIKE, None, None, None, None, None,
        True, True, True, 1.0, False, False,
        "Disabled until forex-like funding/session behavior is explicitly modeled.",
    ),
    AssetClass.UNKNOWN: AssetClassModel(
        AssetClass.UNKNOWN, None, None, None, None, None,
        False, True, True, 0.0, False, False,
        "Unknown instruments fail closed.",
    ),
}


def get_asset_class_model(asset_class: AssetClass | str) -> AssetClassModel:
    if isinstance(asset_class, str):
        try:
            asset_class = AssetClass(asset_class.lower())
        except ValueError:
            asset_class = AssetClass.UNKNOWN
    return _MODELS[asset_class]


def with_symbol_costs(
    model: AssetClassModel,
    *,
    spread_bps: float,
    slippage_bps: float,
    evidence_verified: bool,
) -> AssetClassModel:
    """Return a symbol-specific cost profile without mutating class defaults."""
    if spread_bps < 0 or slippage_bps < 0:
        raise ValueError("spread/slippage cannot be negative")
    return AssetClassModel(
        asset_class=model.asset_class,
        taker_fee_rate=model.taker_fee_rate,
        maker_fee_rate=model.maker_fee_rate,
        assumed_spread_bps=float(spread_bps),
        assumed_slippage_bps=float(slippage_bps),
        expected_funding_interval_hours=model.expected_funding_interval_hours,
        trades_24_7=model.trades_24_7,
        weekend_liquidity_risk=model.weekend_liquidity_risk,
        maintenance_regime_sensitive=model.maintenance_regime_sensitive,
        max_research_leverage=model.max_research_leverage,
        execution_cost_evidence_verified=bool(evidence_verified),
        research_enabled=model.research_enabled,
        notes=model.notes,
    )
