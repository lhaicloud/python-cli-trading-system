"""Fail-closed instrument metadata and asset-class classification for Binance perps."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class AssetClass(str, Enum):
    CRYPTO = "crypto"
    GOLD = "gold"
    SILVER = "silver"
    COMMODITY = "commodity"
    EQUITY = "equity"
    INDEX = "index"
    FOREX_LIKE = "forex_like"
    UNKNOWN = "unknown"


# Explicit mappings for known Binance TradFi families. Unknown contracts do not
# silently inherit CRYPTO behavior; callers must classify them before trading.
_GOLD = {"XAUUSDT"}
_SILVER = {"XAGUSDT"}
_COMMODITIES = {
    "XPTUSDT",
    "XPDUSDT",
    "COPPERUSDT",
    "CLUSDT",
    "BZUSDT",
    "NATGASUSDT",
}


@dataclass(frozen=True)
class InstrumentMetadata:
    symbol: str
    status: str
    contract_type: str
    asset_class: AssetClass
    base_asset: str
    quote_asset: str
    margin_asset: str
    tick_size: float
    step_size: float
    min_qty: float
    min_notional: float | None

    @property
    def tradable(self) -> bool:
        return self.status == "TRADING" and self.asset_class is not AssetClass.UNKNOWN


def classify_asset(symbol: str, raw: dict[str, Any] | None = None) -> AssetClass:
    """Classify conservatively. Unknown TradFi-like contracts stay UNKNOWN.

    Crypto classification requires Binance metadata that looks like a normal
    USDT-margined perpetual and is not one of the explicit TradFi symbols.
    This prevents a new TradFi contract ending in USDT from silently receiving
    crypto assumptions.
    """
    symbol = symbol.upper()
    if symbol in _GOLD:
        return AssetClass.GOLD
    if symbol in _SILVER:
        return AssetClass.SILVER
    if symbol in _COMMODITIES:
        return AssetClass.COMMODITY

    raw = raw or {}
    underlying_type = str(raw.get("underlyingType", "")).upper()
    contract_type = str(raw.get("contractType", "")).upper()
    quote_asset = str(raw.get("quoteAsset", "")).upper()
    margin_asset = str(raw.get("marginAsset", "")).upper()

    # Binance crypto perpetuals normally expose COIN as underlyingType. Keep
    # anything else unclassified until a deliberate mapping is added.
    if (
        underlying_type == "COIN"
        and contract_type == "PERPETUAL"
        and quote_asset == "USDT"
        and margin_asset == "USDT"
    ):
        return AssetClass.CRYPTO

    return AssetClass.UNKNOWN


def parse_instrument_metadata(raw: dict[str, Any]) -> InstrumentMetadata:
    """Parse one exchangeInfo symbol entry and fail closed on missing filters."""
    symbol = str(raw.get("symbol", "")).upper()
    if not symbol:
        raise ValueError("instrument metadata missing symbol")

    filters = {f.get("filterType"): f for f in raw.get("filters", []) if isinstance(f, dict)}
    price_filter = filters.get("PRICE_FILTER")
    lot_filter = filters.get("MARKET_LOT_SIZE") or filters.get("LOT_SIZE")
    notional_filter = filters.get("MIN_NOTIONAL") or filters.get("NOTIONAL")

    if not price_filter or not lot_filter:
        raise ValueError(f"{symbol}: missing PRICE_FILTER or LOT_SIZE metadata")

    try:
        tick_size = float(price_filter["tickSize"])
        step_size = float(lot_filter["stepSize"])
        min_qty = float(lot_filter["minQty"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{symbol}: malformed price/quantity filter metadata") from exc

    if tick_size <= 0 or step_size <= 0 or min_qty < 0:
        raise ValueError(f"{symbol}: invalid non-positive contract precision metadata")

    min_notional: float | None = None
    if notional_filter:
        raw_notional = notional_filter.get("notional", notional_filter.get("minNotional"))
        if raw_notional not in (None, ""):
            try:
                min_notional = float(raw_notional)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{symbol}: malformed minimum notional metadata") from exc

    return InstrumentMetadata(
        symbol=symbol,
        status=str(raw.get("status", "")),
        contract_type=str(raw.get("contractType", "")),
        asset_class=classify_asset(symbol, raw),
        base_asset=str(raw.get("baseAsset", "")),
        quote_asset=str(raw.get("quoteAsset", "")),
        margin_asset=str(raw.get("marginAsset", "")),
        tick_size=tick_size,
        step_size=step_size,
        min_qty=min_qty,
        min_notional=min_notional,
    )


def build_instrument_map(exchange_info: dict[str, Any]) -> dict[str, InstrumentMetadata]:
    """Build a symbol map; malformed entries are not silently defaulted."""
    symbols = exchange_info.get("symbols")
    if not isinstance(symbols, list):
        raise ValueError("exchangeInfo response missing symbols list")

    out: dict[str, InstrumentMetadata] = {}
    for raw in symbols:
        if not isinstance(raw, dict):
            continue
        meta = parse_instrument_metadata(raw)
        out[meta.symbol] = meta
    return out
