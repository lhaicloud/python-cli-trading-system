"""No-network verification for the multi-asset research foundation.

Run from the repository root:

    python -m scripts.verify_multi_asset_foundation

Checks only pure parsing/routing/freshness behavior. It does not place orders,
write the live DB, or call Binance.
"""

from __future__ import annotations

from app.data.binance_client import BinanceClient
from app.data.instruments import AssetClass, classify_asset, parse_instrument_metadata
from app.data.validators import is_timestamp_fresh
from app.utils.timeframes import tf_to_ms

failures = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global failures
    status = "PASS" if condition else "FAIL"
    if not condition:
        failures += 1
    suffix = f" — {detail}" if detail else ""
    print(f"  [{status}] {name}{suffix}")


def sample_symbol(symbol: str, underlying_type: str = "COIN") -> dict:
    return {
        "symbol": symbol,
        "status": "TRADING",
        "contractType": "PERPETUAL",
        "baseAsset": symbol.removesuffix("USDT"),
        "quoteAsset": "USDT",
        "marginAsset": "USDT",
        "underlyingType": underlying_type,
        "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
            {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
            {"filterType": "MIN_NOTIONAL", "notional": "5"},
        ],
    }


print("\n1. Asset classification")
check("XAUUSDT classified GOLD", classify_asset("XAUUSDT") is AssetClass.GOLD)
check("XAGUSDT classified SILVER", classify_asset("XAGUSDT") is AssetClass.SILVER)
check("COPPERUSDT classified COMMODITY", classify_asset("COPPERUSDT") is AssetClass.COMMODITY)
check(
    "normal coin metadata classified CRYPTO",
    classify_asset("BTCUSDT", sample_symbol("BTCUSDT")) is AssetClass.CRYPTO,
)
check(
    "unmapped TradFi-like contract fails closed as UNKNOWN",
    classify_asset("NEWTRADFIUSDT", sample_symbol("NEWTRADFIUSDT", underlying_type="TRADFI"))
    is AssetClass.UNKNOWN,
)

print("\n2. Instrument metadata")
meta = parse_instrument_metadata(sample_symbol("BTCUSDT"))
check("tick size parsed", meta.tick_size == 0.10)
check("step size parsed", meta.step_size == 0.001)
check("minimum quantity parsed", meta.min_qty == 0.001)
check("minimum notional parsed", meta.min_notional == 5.0)
check("known crypto metadata is tradable", meta.tradable)

bad = sample_symbol("BADUSDT")
bad["filters"] = []
try:
    parse_instrument_metadata(bad)
except ValueError:
    missing_filters_failed_closed = True
else:
    missing_filters_failed_closed = False
check("missing precision filters fail closed", missing_filters_failed_closed)

print("\n3. Candle freshness")
now = 2_000_000_000_000
one_hour = tf_to_ms("1h")
check("recent 1h candle accepted", is_timestamp_fresh(now - one_hour, now, "1h"))
check("stale 1h candle rejected", not is_timestamp_fresh(now - 4 * one_hour, now, "1h"))
check("future candle rejected", not is_timestamp_fresh(now + 1, now, "1h"))

print("\n4. Public endpoint routing")
client = object.__new__(BinanceClient)
client._limit = 1000
calls: list[tuple[str, dict | None]] = []


def fake_get(path: str, params: dict | None = None):
    calls.append((path, params))
    if path == "/fapi/v1/ticker/bookTicker":
        return {"symbol": "BTCUSDT", "bidPrice": "1", "askPrice": "2"}
    if path == "/fapi/v1/fundingInfo":
        return []
    return []


client._get = fake_get  # type: ignore[method-assign]
client.get_mark_price_klines("BTCUSDT", "1h", 1, 2)
client.get_index_price_klines("BTCUSDT", "1h", 1, 2)
client.get_premium_index_klines("BTCUSDT", "1h", 1, 2)
client.get_book_ticker("BTCUSDT")
client.get_funding_info("BTCUSDT")

paths = [p for p, _ in calls]
check("mark-price kline endpoint", "/fapi/v1/markPriceKlines" in paths)
check("index-price kline endpoint", "/fapi/v1/indexPriceKlines" in paths)
check("premium-index kline endpoint", "/fapi/v1/premiumIndexKlines" in paths)
check("book-ticker endpoint", "/fapi/v1/ticker/bookTicker" in paths)
check("funding-info endpoint", "/fapi/v1/fundingInfo" in paths)
index_call = next(params for path, params in calls if path == "/fapi/v1/indexPriceKlines")
check("index endpoint uses pair parameter", bool(index_call and index_call.get("pair") == "BTCUSDT"))

print(f"\n{'ALL CHECKS PASSED' if failures == 0 else f'{failures} CHECK(S) FAILED'}")
raise SystemExit(1 if failures else 0)
