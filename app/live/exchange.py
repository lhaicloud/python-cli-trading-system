"""
Authenticated Binance USDT-Margined Futures REST client.

Handles HMAC-SHA256 signing, symbol precision caching, and all order operations
needed by LiveExecutor. Exchange info is fetched once at __init__ and cached
in-memory for the process lifetime.

Testnet:  BINANCE_TESTNET=true  →  testnet.binancefuture.com
Live:                            →  fapi.binance.com
"""

from __future__ import annotations

import hashlib
import hmac
import math
import time
from typing import Any
from urllib.parse import urlencode

import httpx

from app.utils.logger import get_logger

logger = get_logger(__name__)

_LIVE_REST    = "https://fapi.binance.com"
_TESTNET_REST = "https://testnet.binancefuture.com"

# Approximate request weight per endpoint (used for logging only).
_WEIGHTS: dict[str, int] = {
    "/fapi/v1/order":           1,
    "/fapi/v1/allOpenOrders":   1,
    "/fapi/v1/openOrders":      1,
    "/fapi/v1/leverage":        1,
    "/fapi/v1/listenKey":       1,
    "/fapi/v2/balance":         5,
    "/fapi/v2/positionRisk":    5,
    "/fapi/v1/positionSide/dual": 1,
    "/fapi/v1/exchangeInfo":    40,
    "/fapi/v1/algoOrder":       1,
    "/fapi/v1/openAlgoOrders":  1,
}


class BinanceExchangeClient:
    """Authenticated Binance Futures REST client."""

    def __init__(self, api_key: str, api_secret: str, testnet: bool = False) -> None:
        self._api_key    = api_key
        self._api_secret = api_secret
        self._base       = _TESTNET_REST if testnet else _LIVE_REST
        self._testnet    = testnet
        self._http       = httpx.Client(timeout=15)
        # symbol → {"lot_step": float, "tick_size": float}
        self._sym_info: dict[str, dict[str, float]] = {}
        self._load_symbol_info()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _sign(self, params: dict) -> str:
        query = urlencode(params)
        return hmac.new(
            self._api_secret.encode(),
            query.encode(),
            hashlib.sha256,
        ).hexdigest()

    def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        signed: bool = True,
    ) -> Any:
        params = dict(params or {})
        if signed:
            params["timestamp"] = int(time.time() * 1000)
            params["signature"] = self._sign(params)

        url     = self._base + path
        headers = {"X-MBX-APIKEY": self._api_key}
        weight  = _WEIGHTS.get(path, 1)

        try:
            if method == "GET":
                resp = self._http.get(url, params=params, headers=headers)
            elif method == "POST":
                resp = self._http.post(url, params=params, headers=headers)
            elif method == "PUT":
                resp = self._http.put(url, params=params, headers=headers)
            elif method == "DELETE":
                resp = self._http.delete(url, params=params, headers=headers)
            else:
                raise ValueError(f"Unknown method: {method}")

            if weight >= 10:
                logger.debug("[Exchange] %s %s  weight=%d", method, path, weight)

            if resp.status_code != 200:
                logger.error(
                    "[Exchange] %s %s → HTTP %d: %s",
                    method, path, resp.status_code, resp.text[:300],
                )
                resp.raise_for_status()

            return resp.json()

        except httpx.HTTPStatusError:
            raise
        except Exception as exc:
            logger.error("[Exchange] %s %s failed: %s", method, path, exc)
            raise

    def _load_symbol_info(self) -> None:
        """Fetch LOT_SIZE and PRICE_FILTER for all symbols once at startup."""
        try:
            data = self._request("GET", "/fapi/v1/exchangeInfo", signed=False)
            for sym in data.get("symbols", []):
                symbol = sym["symbol"]
                lot_step = tick_size = None
                for f in sym.get("filters", []):
                    if f["filterType"] == "LOT_SIZE":
                        lot_step = float(f["stepSize"])
                    elif f["filterType"] == "PRICE_FILTER":
                        tick_size = float(f["tickSize"])
                if lot_step and tick_size:
                    self._sym_info[symbol] = {
                        "lot_step":  lot_step,
                        "tick_size": tick_size,
                    }
            logger.info("[Exchange] Symbol info cached for %d symbols", len(self._sym_info))
        except Exception as exc:
            logger.error("[Exchange] Failed to load symbol info: %s", exc)

    def _step_for(self, symbol: str) -> float:
        return self._sym_info.get(symbol, {}).get("lot_step", 0.001)

    def _tick_for(self, symbol: str) -> float:
        return self._sym_info.get(symbol, {}).get("tick_size", 0.01)

    # ── Precision helpers ─────────────────────────────────────────────────────

    def round_qty(self, symbol: str, qty: float) -> float:
        step = self._step_for(symbol)
        if step <= 0:
            return qty
        precision = max(0, -int(math.floor(math.log10(step))))
        return round(math.floor(qty / step) * step, precision)

    def round_price(self, symbol: str, price: float) -> float:
        tick = self._tick_for(symbol)
        if tick <= 0:
            return price
        precision = max(0, -int(math.floor(math.log10(tick))))
        return round(round(price / tick) * tick, precision)

    # ── Account ───────────────────────────────────────────────────────────────

    def get_balance(self) -> float:
        """Return available free USDT balance."""
        data = self._request("GET", "/fapi/v2/balance")
        for asset in data:
            if asset.get("asset") == "USDT":
                return float(asset.get("availableBalance", 0))
        return 0.0

    def get_wallet_balance(self) -> dict:
        """Return full USDT balance info: wallet, unrealizedProfit, availableBalance."""
        data = self._request("GET", "/fapi/v2/balance")
        for asset in data:
            if asset.get("asset") == "USDT":
                return {
                    "wallet":      float(asset.get("balance", 0)),
                    "unrealized":  float(asset.get("crossUnPnl", 0)),
                    "available":   float(asset.get("availableBalance", 0)),
                    "margin_used": float(asset.get("balance", 0)) - float(asset.get("availableBalance", 0)),
                }
        return {"wallet": 0.0, "unrealized": 0.0, "available": 0.0, "margin_used": 0.0}

    def get_equity(self) -> float:
        """Return true account equity (wallet balance + unrealized PnL) — unlike
        get_balance()'s availableBalance, this doesn't swing with locked order margin."""
        bal = self.get_wallet_balance()
        return bal["wallet"] + bal["unrealized"]

    def get_position(self, symbol: str) -> dict | None:
        """Return open position info for symbol, or None if flat."""
        data = self._request("GET", "/fapi/v2/positionRisk", {"symbol": symbol})
        for pos in data:
            if pos.get("symbol") == symbol:
                amt = float(pos.get("positionAmt", 0))
                if amt != 0:
                    return pos
        return None

    def get_position_mode(self) -> str:
        """Return 'one-way' or 'hedge'."""
        data = self._request("GET", "/fapi/v1/positionSide/dual")
        return "hedge" if data.get("dualSidePosition") else "one-way"

    def set_leverage(self, symbol: str, leverage: int) -> None:
        self._request("POST", "/fapi/v1/leverage", {
            "symbol":   symbol,
            "leverage": leverage,
        })
        logger.debug("[Exchange] Set leverage %d× for %s", leverage, symbol)

    def get_open_orders(self, symbol: str) -> list[dict]:
        return self._request("GET", "/fapi/v1/openOrders", {"symbol": symbol})

    def get_mark_price(self, symbol: str) -> float:
        """Return the current mark price (what MARK_PRICE brackets trigger on)."""
        data = self._request("GET", "/fapi/v1/premiumIndex", {"symbol": symbol}, signed=False)
        return float(data.get("markPrice") or 0)

    # ── Orders ────────────────────────────────────────────────────────────────

    def place_market_order(
        self, symbol: str, side: str, quantity: float, reduce_only: bool = False
    ) -> dict:
        """Place a market order. Returns the full order response including avgPrice.

        reduce_only=True exempts the order from the min-notional filter, so
        even dust-sized position remainders can be closed.
        """
        params = {
            "symbol":   symbol,
            "side":     side,       # BUY or SELL
            "type":     "MARKET",
            "quantity": quantity,
        }
        if reduce_only:
            params["reduceOnly"] = "true"
        result = self._request("POST", "/fapi/v1/order", params)
        logger.info(
            "[Exchange] MARKET %s %s qty=%s  avgPrice=%s",
            side, symbol, quantity, result.get("avgPrice"),
        )
        return result

    def place_limit_order(
        self, symbol: str, side: str, quantity: float, price: float,
        reduce_only: bool = False,
    ) -> dict:
        """Place a GTC limit order. Returns the order response (includes orderId)."""
        params = {
            "symbol":      symbol,
            "side":        side,       # BUY or SELL
            "type":        "LIMIT",
            "timeInForce": "GTC",
            "quantity":    quantity,
            "price":       self.round_price(symbol, price),
        }
        if reduce_only:
            params["reduceOnly"] = "true"
        result = self._request("POST", "/fapi/v1/order", params)
        logger.info(
            "[Exchange] LIMIT %s %s qty=%s price=%s orderId=%s",
            side, symbol, quantity, price, result.get("orderId"),
        )
        return result

    def place_stop_market(
        self,
        symbol: str,
        side: str,
        stop_price: float,
        quantity: float,
    ) -> dict:
        """Place a STOP_MARKET conditional order via the Algo Order API (reduceOnly=True)."""
        params = {
            "algoType":         "CONDITIONAL",
            "symbol":           symbol,
            "side":             side,
            "type":             "STOP_MARKET",
            "triggerPrice":     self.round_price(symbol, stop_price),
            "quantity":         quantity,
            "reduceOnly":       "true",
            "workingType":      "MARK_PRICE",
        }
        result = self._request("POST", "/fapi/v1/algoOrder", params)
        logger.info(
            "[Exchange] STOP_MARKET %s %s qty=%s  stop=%s  algoId=%s",
            side, symbol, quantity, stop_price, result.get("algoId"),
        )
        return result

    def place_take_profit_market(
        self,
        symbol: str,
        side: str,
        stop_price: float,
        quantity: float,
    ) -> dict:
        """Place a TAKE_PROFIT_MARKET conditional order via the Algo Order API (reduceOnly=True)."""
        params = {
            "algoType":    "CONDITIONAL",
            "symbol":      symbol,
            "side":        side,
            "type":        "TAKE_PROFIT_MARKET",
            "triggerPrice": self.round_price(symbol, stop_price),
            "quantity":    quantity,
            "reduceOnly":  "true",
            "workingType": "MARK_PRICE",
        }
        result = self._request("POST", "/fapi/v1/algoOrder", params)
        logger.info(
            "[Exchange] TAKE_PROFIT_MARKET %s %s qty=%s  tp=%s  algoId=%s",
            side, symbol, quantity, stop_price, result.get("algoId"),
        )
        return result

    def cancel_order(self, symbol: str, order_id: int | str) -> dict:
        try:
            result = self._request("DELETE", "/fapi/v1/order", {
                "symbol":  symbol,
                "orderId": order_id,
            })
            logger.debug("[Exchange] Cancelled order %s for %s", order_id, symbol)
            return result
        except Exception as exc:
            logger.warning("[Exchange] Cancel order %s failed: %s", order_id, exc)
            return {}

    def cancel_algo_order(self, symbol: str, algo_id: int | str) -> dict:
        try:
            result = self._request("DELETE", "/fapi/v1/algoOrder", {
                "symbol": symbol,
                "algoId": algo_id,
            })
            # info, not debug: cancel success must be auditable in journald —
            # a missed cancel here means a stale bracket resting on the exchange
            logger.info("[Exchange] Cancelled algo order %s for %s", algo_id, symbol)
            return result
        except Exception as exc:
            logger.warning("[Exchange] Cancel algo order %s failed: %s", algo_id, exc)
            return {}

    def get_algo_order(self, symbol: str, algo_id: int | str) -> dict:
        return self._request("GET", "/fapi/v1/algoOrder", {
            "symbol": symbol,
            "algoId": algo_id,
        })

    def get_open_algo_orders(self, symbol: str) -> list[dict]:
        return self._request("GET", "/fapi/v1/openAlgoOrders", {"symbol": symbol})

    def cancel_all_orders(self, symbol: str) -> None:
        try:
            self._request("DELETE", "/fapi/v1/allOpenOrders", {"symbol": symbol})
            logger.info("[Exchange] Cancelled all orders for %s", symbol)
        except Exception as exc:
            logger.warning("[Exchange] cancel_all_orders %s failed: %s", symbol, exc)

    # ── User Data Stream ──────────────────────────────────────────────────────

    def get_listen_key(self) -> str:
        data = self._request("POST", "/fapi/v1/listenKey")
        return data["listenKey"]

    def keepalive_listen_key(self, listen_key: str) -> None:
        try:
            self._request("PUT", "/fapi/v1/listenKey", {"listenKey": listen_key})
        except Exception as exc:
            logger.warning("[Exchange] listenKey keepalive failed: %s", exc)

    # ── WebSocket URL ─────────────────────────────────────────────────────────

    @property
    def ws_base(self) -> str:
        if self._testnet:
            return "wss://stream.binancefuture.com"
        return "wss://fstream.binance.com"
