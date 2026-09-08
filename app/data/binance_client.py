"""Binance public REST API client — no auth required for market data."""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.config import get_settings
from app.utils.logger import get_logger
from app.utils.timeframes import tf_to_ms

logger = get_logger(__name__)

# Binance rate-limit: 1200 weight/min on /api endpoint.
_REQUEST_PAUSE_S = 0.1
_MAX_RETRIES = 5
_RETRY_BACKOFF_S = 2.0


def _redact_proxy(url: str) -> str:
    """Mask user:pass credentials in a proxy URL before logging."""
    if "@" in url and "://" in url:
        scheme, rest = url.split("://", 1)
        return f"{scheme}://***@{rest.split('@', 1)[1]}"
    return url


class BinanceClient:
    """Thin wrapper around Binance USDⓈ-M public REST endpoints."""

    def __init__(self) -> None:
        cfg = get_settings()
        self._base = cfg.binance_base_url.rstrip("/")
        self._limit = cfg.binance_kline_limit
        proxy = cfg.binance_proxy_url.strip() or None
        if proxy:
            logger.info("Routing Binance traffic through proxy %s", _redact_proxy(proxy))
        self._client = httpx.Client(timeout=30.0, proxy=proxy)

    # ── Public helpers ────────────────────────────────────────────────────────

    def get_exchange_info(self) -> dict[str, Any]:
        return self._get("/fapi/v1/exchangeInfo")

    def get_server_time(self) -> int:
        """Return Binance server time as Unix ms."""
        data = self._get("/fapi/v1/time")
        return int(data["serverTime"])

    @staticmethod
    def _kline_params(
        *,
        key: str,
        value: str,
        interval: str,
        start_ms: int | None,
        end_ms: int | None,
        limit: int,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            key: value.upper(),
            "interval": interval,
            "limit": min(limit, 1000),
        }
        if start_ms is not None:
            params["startTime"] = start_ms
        if end_ms is not None:
            params["endTime"] = end_ms
        return params

    def get_klines(
        self,
        symbol: str,
        interval: str,
        start_ms: int | None = None,
        end_ms: int | None = None,
        limit: int | None = None,
    ) -> list[list[Any]]:
        """Fetch trade-price futures klines."""
        params = self._kline_params(
            key="symbol",
            value=symbol,
            interval=interval,
            start_ms=start_ms,
            end_ms=end_ms,
            limit=limit or self._limit,
        )
        return self._get("/fapi/v1/klines", params=params)  # type: ignore[return-value]

    def get_mark_price_klines(
        self,
        symbol: str,
        interval: str,
        start_ms: int | None = None,
        end_ms: int | None = None,
        limit: int | None = None,
    ) -> list[list[Any]]:
        """Fetch historical mark-price klines for liquidation/fair-price research."""
        params = self._kline_params(
            key="symbol",
            value=symbol,
            interval=interval,
            start_ms=start_ms,
            end_ms=end_ms,
            limit=limit or self._limit,
        )
        return self._get("/fapi/v1/markPriceKlines", params=params)  # type: ignore[return-value]

    def get_index_price_klines(
        self,
        pair: str,
        interval: str,
        start_ms: int | None = None,
        end_ms: int | None = None,
        limit: int | None = None,
    ) -> list[list[Any]]:
        """Fetch historical index-price klines. Binance names this key `pair`."""
        params = self._kline_params(
            key="pair",
            value=pair,
            interval=interval,
            start_ms=start_ms,
            end_ms=end_ms,
            limit=limit or self._limit,
        )
        return self._get("/fapi/v1/indexPriceKlines", params=params)  # type: ignore[return-value]

    def get_premium_index_klines(
        self,
        symbol: str,
        interval: str,
        start_ms: int | None = None,
        end_ms: int | None = None,
        limit: int | None = None,
    ) -> list[list[Any]]:
        """Fetch historical premium-index klines."""
        params = self._kline_params(
            key="symbol",
            value=symbol,
            interval=interval,
            start_ms=start_ms,
            end_ms=end_ms,
            limit=limit or self._limit,
        )
        return self._get("/fapi/v1/premiumIndexKlines", params=params)  # type: ignore[return-value]

    def get_all_klines(
        self,
        symbol: str,
        interval: str,
        start_ms: int,
        end_ms: int,
    ) -> list[list[Any]]:
        """Paginate trade-price klines in ascending open-time order."""
        all_klines: list[list[Any]] = []
        current_start = start_ms
        interval_ms = tf_to_ms(interval)

        while current_start < end_ms:
            batch = self.get_klines(
                symbol=symbol,
                interval=interval,
                start_ms=current_start,
                end_ms=end_ms,
                limit=self._limit,
            )
            if not batch:
                break

            all_klines.extend(batch)
            last_open_time = int(batch[-1][0])
            if len(batch) < self._limit:
                break

            current_start = last_open_time + interval_ms
            time.sleep(_REQUEST_PAUSE_S)

        return all_klines

    def get_ticker_price(self, symbol: str) -> float:
        data = self._get("/fapi/v1/ticker/price", params={"symbol": symbol.upper()})
        return float(data["price"])

    def get_book_ticker(self, symbol: str) -> dict[str, Any]:
        """Return public best bid/ask for executable-spread research."""
        data = self._get("/fapi/v1/ticker/bookTicker", params={"symbol": symbol.upper()})
        if not isinstance(data, dict):
            raise ValueError(f"Unexpected bookTicker response for {symbol}")
        return data

    def get_premium_index(self, symbol: str) -> dict[str, Any]:
        """Return current mark/index/funding snapshot from premiumIndex."""
        data = self._get("/fapi/v1/premiumIndex", params={"symbol": symbol.upper()})
        if not isinstance(data, dict):
            raise ValueError(f"Unexpected premiumIndex response for {symbol}")
        return data

    def get_mark_price(self, symbol: str) -> float:
        """Return current mark price."""
        data = self.get_premium_index(symbol)
        return float(data.get("markPrice") or 0)

    def get_funding_rate(self, symbol: str) -> float:
        """Return current funding rate (positive means longs pay shorts)."""
        data = self.get_premium_index(symbol)
        return float(data.get("lastFundingRate", 0) or 0)

    def get_funding_info(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """Return current non-default funding interval/cap/floor information."""
        params = {"symbol": symbol.upper()} if symbol else None
        data = self._get("/fapi/v1/fundingInfo", params=params)
        if not isinstance(data, list):
            raise ValueError("Unexpected fundingInfo response")
        return data

    def get_funding_history(
        self,
        symbol: str,
        start_ms: int,
        end_ms: int,
    ) -> list[dict[str, Any]]:
        """Historical funding rates, paginated in ascending settlement time."""
        out: list[dict[str, Any]] = []
        cursor = start_ms
        while cursor < end_ms:
            batch = self._get("/fapi/v1/fundingRate", params={
                "symbol": symbol.upper(),
                "startTime": cursor,
                "endTime": end_ms,
                "limit": 1000,
            })
            if not batch:
                break
            for row in batch:
                out.append({
                    "funding_time": int(row["fundingTime"]),
                    "rate": float(row.get("fundingRate", 0) or 0),
                })
            if len(batch) < 1000:
                break
            cursor = int(batch[-1]["fundingTime"]) + 1
            time.sleep(_REQUEST_PAUSE_S)
        return out

    # ── Internal ──────────────────────────────────────────────────────────────

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self._base}{path}"
        for attempt in range(_MAX_RETRIES):
            try:
                resp = self._client.get(url, params=params)
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429:
                    wait = _RETRY_BACKOFF_S * (2 ** attempt)
                    logger.warning("Rate limited — waiting %.1fs", wait)
                    time.sleep(wait)
                else:
                    logger.error("HTTP %s for %s: %s", exc.response.status_code, url, exc)
                    raise
            except httpx.RequestError as exc:
                wait = _RETRY_BACKOFF_S * (2 ** attempt)
                logger.warning("Request error (%s) — retry %d in %.1fs", exc, attempt + 1, wait)
                time.sleep(wait)
        raise RuntimeError(f"Failed to GET {url} after {_MAX_RETRIES} retries")

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "BinanceClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
