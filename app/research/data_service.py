"""Read-only Binance research backfill/capture services."""

from __future__ import annotations

import time
from typing import Any, Callable

from app.data.binance_client import BinanceClient
from app.data.instruments import parse_instrument_metadata
from app.research.storage import ResearchStore
from app.utils.timeframes import tf_to_ms

_SERIES_METHODS = {
    "trade": "get_klines",
    "mark": "get_mark_price_klines",
    "index": "get_index_price_klines",
    "premium": "get_premium_index_klines",
}


def snapshot_instrument_metadata(
    client: BinanceClient,
    store: ResearchStore,
    observed_at_ms: int | None = None,
) -> int:
    """Persist an exchangeInfo snapshot. Malformed contract metadata aborts."""
    observed_at_ms = observed_at_ms or client.get_server_time()
    info = client.get_exchange_info()
    symbols = info.get("symbols")
    if not isinstance(symbols, list):
        raise ValueError("exchangeInfo response missing symbols list")

    saved = 0
    for raw in symbols:
        if not isinstance(raw, dict):
            raise ValueError("exchangeInfo contains malformed symbol entry")
        meta = parse_instrument_metadata(raw)
        store.save_instrument_metadata(meta, observed_at_ms, raw)
        saved += 1
    return saved


def backfill_price_series(
    client: BinanceClient,
    store: ResearchStore,
    *,
    series_type: str,
    symbol: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
    limit: int = 1000,
) -> int:
    """Backfill only fully closed trade/mark/index/premium klines."""
    if series_type not in _SERIES_METHODS:
        raise ValueError(f"unsupported research series_type: {series_type}")
    if start_ms >= end_ms:
        return 0

    interval_ms = tf_to_ms(timeframe)
    server_time = client.get_server_time()
    last_closed_end = (server_time // interval_ms) * interval_ms - 1
    effective_end = min(end_ms, last_closed_end)
    if start_ms > effective_end:
        return 0

    method: Callable[..., list[list[Any]]] = getattr(client, _SERIES_METHODS[series_type])
    cursor = start_ms
    inserted = 0

    while cursor <= effective_end:
        kwargs = {
            "interval": timeframe,
            "start_ms": cursor,
            "end_ms": effective_end,
            "limit": limit,
        }
        if series_type == "index":
            batch = method(pair=symbol, **kwargs)
        else:
            batch = method(symbol=symbol, **kwargs)

        if not batch:
            break

        # Binance can return a currently forming row near the boundary. Store
        # only rows whose close timestamp is known to be complete.
        closed_batch = [row for row in batch if len(row) >= 7 and int(row[6]) <= effective_end]
        if closed_batch:
            inserted += store.upsert_price_klines(series_type, symbol, timeframe, closed_batch)

        last_open = int(batch[-1][0])
        next_cursor = last_open + interval_ms
        if next_cursor <= cursor:
            raise RuntimeError("Binance kline pagination did not advance")
        cursor = next_cursor

        if len(batch) < limit:
            break
        time.sleep(0.1)

    return inserted


def backfill_funding(
    client: BinanceClient,
    store: ResearchStore,
    *,
    symbol: str,
    start_ms: int,
    end_ms: int,
) -> int:
    rows = client.get_funding_history(symbol, start_ms, end_ms)
    return store.upsert_funding_rates(symbol, rows)


def snapshot_funding_info(
    client: BinanceClient,
    store: ResearchStore,
    observed_at_ms: int | None = None,
) -> int:
    """Persist current Binance funding-info exceptions for historical versioning."""
    observed_at_ms = observed_at_ms or client.get_server_time()
    rows = client.get_funding_info()
    saved = 0
    for raw in rows:
        if not isinstance(raw, dict) or not raw.get("symbol"):
            raise ValueError("malformed fundingInfo row")
        store.save_funding_info(str(raw["symbol"]), observed_at_ms, raw)
        saved += 1
    return saved


def capture_book_ticker(
    client: BinanceClient,
    store: ResearchStore,
    symbol: str,
    captured_at_ms: int | None = None,
) -> int:
    """Capture one best bid/ask sample; timestamp is made monotonically unique."""
    raw = client.get_book_ticker(symbol)
    captured_at_ms = captured_at_ms or int(time.time() * 1000)
    try:
        bid = float(raw["bidPrice"])
        bid_qty = float(raw.get("bidQty", 0) or 0)
        ask = float(raw["askPrice"])
        ask_qty = float(raw.get("askQty", 0) or 0)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"malformed bookTicker response for {symbol}") from exc
    return store.save_book_ticker(symbol, captured_at_ms, bid, bid_qty, ask, ask_qty)
