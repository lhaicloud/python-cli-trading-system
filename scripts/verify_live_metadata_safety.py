"""No-network regression tests for fail-closed live contract precision."""

from __future__ import annotations

from app.live.exchange import BinanceExchangeClient, SymbolMetadataError

failures = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global failures
    status = "PASS" if condition else "FAIL"
    if not condition:
        failures += 1
    print(f"  [{status}] {name}{(' — ' + detail) if detail else ''}")


def client_with(info):
    client = BinanceExchangeClient.__new__(BinanceExchangeClient)
    client._sym_info = info
    return client


print("\n1. Valid precision")
client = client_with({"BTCUSDT": {"lot_step": 0.001, "tick_size": 0.1}})
check("quantity rounds to exchange step", client.round_qty("BTCUSDT", 1.23456) == 1.234)
check("price rounds to exchange tick", client.round_price("BTCUSDT", 100.04) == 100.0)

print("\n2. Missing metadata fails closed")
try:
    client.round_qty("UNKNOWNUSDT", 1.0)
except SymbolMetadataError:
    missing_qty_rejected = True
else:
    missing_qty_rejected = False
check("no default quantity step", missing_qty_rejected)

try:
    client.round_price("UNKNOWNUSDT", 100.0)
except SymbolMetadataError:
    missing_price_rejected = True
else:
    missing_price_rejected = False
check("no default price tick", missing_price_rejected)

print("\n3. Invalid metadata fails closed")
bad = client_with({"BADUSDT": {"lot_step": 0.0, "tick_size": 0.1}})
try:
    bad.round_qty("BADUSDT", 1.0)
except SymbolMetadataError:
    bad_rejected = True
else:
    bad_rejected = False
check("zero step rejected", bad_rejected)

print("\n4. Order request blocked before transport")
blocked = client_with({})
called = False

def request(*args, **kwargs):
    global called
    called = True
    raise AssertionError("transport should not be called")

blocked._request = request
try:
    blocked.place_market_order("MISSINGUSDT", "BUY", 1.0)
except SymbolMetadataError:
    preflight_rejected = True
else:
    preflight_rejected = False
check("market order rejected before request", preflight_rejected)
check("transport never called", not called)

print(f"\n{'ALL CHECKS PASSED' if failures == 0 else f'{failures} CHECK(S) FAILED'}")
raise SystemExit(1 if failures else 0)
