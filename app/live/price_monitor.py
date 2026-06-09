"""
PriceMonitor — background thread for intrabar SL/TP detection.

Runs independently of the 30-m signal loop.  Polls Binance ticker price
every POLL_INTERVAL_S seconds for any symbol that has an open trade, then
calls position_manager.check_price() so SL/TP events are detected within
~60 s instead of up to 30 min.

Usage:
    monitor = PriceMonitor(symbols, position_manager, on_trade_closed=callback)
    monitor.start()
    ...
    monitor.stop()
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from app.config import get_settings
from app.paper.position_manager import PositionManager
from app.utils.logger import get_logger

logger = get_logger(__name__)

POLL_INTERVAL_S = 60   # seconds between price checks


class PriceMonitor:
    """
    Daemon thread that polls live prices and triggers SL/TP checks between
    30-m candle closes.

    Parameters
    ----------
    symbols          : Full watchlist — monitor only polls symbols with open trades.
    position_manager : PositionManager instance shared with the watcher.
    on_trade_closed  : Callback(closed_trade: dict, symbol: str) — called for each
                       trade closed by the monitor (capital update + Telegram).
    poll_interval_s  : Seconds between polls (default 60).
    """

    def __init__(
        self,
        symbols: list[str],
        position_manager: PositionManager,
        on_trade_closed: Callable[[dict, str], None] | None = None,
        poll_interval_s: int = POLL_INTERVAL_S,
    ) -> None:
        self.symbols          = [s.upper() for s in symbols]
        self._pm              = position_manager
        self._on_closed       = on_trade_closed
        self._poll_interval_s = poll_interval_s
        self._stop_event      = threading.Event()
        self._thread: threading.Thread | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background polling thread."""
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="PriceMonitor",
            daemon=True,
        )
        self._thread.start()
        logger.info("[PriceMonitor] Started — polling every %ds", self._poll_interval_s)

    def stop(self) -> None:
        """Signal the thread to stop and wait for it to finish."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("[PriceMonitor] Stopped")

    def update_symbols(self, symbols: list[str]) -> None:
        """Update the watchlist (called when rescan changes self.symbols)."""
        self.symbols = [s.upper() for s in symbols]

    # ── Poll loop ─────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._poll_all()
            except Exception as exc:
                logger.warning("[PriceMonitor] Unhandled error: %s", exc)

            # Sleep in short increments so stop_event is checked promptly
            for _ in range(self._poll_interval_s):
                if self._stop_event.is_set():
                    return
                time.sleep(1)

    def _poll_all(self) -> None:
        """Poll price for every symbol that currently has an open trade."""
        # Only import/use client for symbols with open trades
        active = [
            sym for sym in self.symbols
            if self._pm.open_trades(sym)
        ]
        if not active:
            return

        try:
            from app.data.binance_client import BinanceClient
            with BinanceClient() as client:
                for sym in active:
                    if self._stop_event.is_set():
                        return
                    try:
                        price = client.get_ticker_price(sym)
                        closed = self._pm.check_price(sym, price)
                        for trade in closed:
                            if self._on_closed:
                                self._on_closed(trade, sym)
                    except Exception as exc:
                        logger.warning(
                            "[PriceMonitor][%s] Price check failed: %s", sym, exc
                        )
        except Exception as exc:
            logger.warning("[PriceMonitor] Client error: %s", exc)
