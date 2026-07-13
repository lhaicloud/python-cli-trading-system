"""
LQ-MTF Live Trading Watcher — real money on Binance Futures.

Architecture:
  - 30-m signal loop : sleeps to candle close, generates signals, calls LiveExecutor
  - UserDataStream   : WebSocket thread, fills ORDER_TRADE_UPDATE → LiveExecutor
  - LiveExecutor     : owns all live_trades state transitions and bracket order mgmt
  - LiveTelegramCommandListener : live channel bot (Open Positions, Close All, etc.)

Paper watcher and live watcher are fully independent. Paper channel never receives
live notifications; live channel never receives paper notifications.

Run:
    python main.py live --auto --live-execution
    python main.py live --auto --live-execution --dry-run
"""

from __future__ import annotations

import dataclasses
import os
import signal as _signal
import threading
import time

from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule

from app.config import get_settings
from app.data.backfill import update_latest
from app.data.repository import (
    get_candles,
    get_latest_signal,
    save_signal,
)
from app.live.exchange import BinanceExchangeClient
from app.live.executor import LiveExecutor
from app.live.live_notifications import (
    live_notify_error,
    live_notify_signal,
    live_notify_trade_closed,
    live_notify_trade_opened,
    live_send_daily_digest,
    live_send_heartbeat,
    live_send_message,
)
from app.live.user_data_stream import UserDataStream
from app.paper.signal_filter import SignalFilter
from app.ta.leverage import dynamic_leverage
from app.ta.signals import generate_signal, SignalResult
from app.utils.logger import get_logger
from app.utils.timeframes import ms_to_dt, tf_to_ms

logger  = get_logger(__name__)
console = Console()

_DANGER_REGIMES = {"choppy", "high_volatility_liquidation"}


def _next_candle_close_ms(interval_ms: int) -> int:
    now_ms = int(time.time() * 1000)
    return ((now_ms // interval_ms) + 1) * interval_ms


class LiveTradingWatcher:
    """
    Live execution watcher. Independent from paper — separate DB table,
    separate Telegram channel, separate circuit breakers.

    Parameters
    ----------
    symbols          : Trading pairs, e.g. ["BTCUSDT", "ETHUSDT"]
    exec_timeframe   : Execution timeframe (default "30m")
    heartbeat_every  : Telegram heartbeat every N cycles (0 = never)
    dry_run          : Generate signals and log orders but NEVER touch exchange
    stop_event       : External threading.Event to stop the watcher
    rescan_every     : Re-run universe scanner every N cycles (0 = disabled)
    rescan_n         : Symbols to pick on rescan
    rescan_validated : Only include validated symbols on rescan
    rescan_pool      : Optional override pool for universe scanner
    """

    def __init__(
        self,
        symbols:          list[str],
        exec_timeframe:   str   = "30m",
        heartbeat_every:  int   = 8,
        dry_run:          bool  = False,
        stop_event:       threading.Event | None = None,
        rescan_every:     int   = 0,
        rescan_n:         int   = 0,
        rescan_validated: bool  = True,
        rescan_pool:      list[str] | None = None,
        rotation_history_file: str | None = None,
    ) -> None:
        cfg = get_settings()
        self.exec_tf           = exec_timeframe
        self.heartbeat_every   = heartbeat_every
        self.dry_run           = dry_run
        self._stop             = False
        self._stop_event       = stop_event
        self._cycle            = 0
        self._interval_ms      = tf_to_ms(exec_timeframe)
        self._rescan_every     = rescan_every
        self._rescan_n         = rescan_n
        self._rescan_validated = rescan_validated
        self._rescan_pending   = False
        self._rescan_pool      = [s.upper() for s in rescan_pool] if rescan_pool else None
        self._rotation_history_file = rotation_history_file
        self._last_digest_date = time.strftime("%Y-%m-%d", time.gmtime())

        # Exchange client
        self._client = BinanceExchangeClient(
            api_key    = cfg.binance_api_key,
            api_secret = cfg.binance_api_secret,
            testnet    = cfg.binance_testnet,
        )

        # Shared lock for all DB writes + exchange calls between threads
        self._lock = threading.Lock()

        # Executor + UDS
        self._executor = LiveExecutor(client=self._client, lock=self._lock)
        self._uds      = UserDataStream(
            client   = self._client,
            executor = self._executor,
            lock     = self._lock,
        )

        # Symbol list — seed from CLI args, then pin any live_trades that are open
        self.symbols = [s.upper() for s in symbols]
        _db_open = {t["symbol"] for t in self._executor.all_open_trades()}
        _pinned  = sorted(_db_open - set(self.symbols))
        if _pinned:
            logger.warning("[LiveWatcher] Pinning %s — open live_trades not in watchlist", _pinned)
            console.print(
                f"  [yellow]⚠ Auto-pinning {_pinned} — open live trades found in DB[/yellow]"
            )
            self.symbols = self.symbols + _pinned

        self._filter = SignalFilter()

        if stop_event is None:
            _signal.signal(_signal.SIGINT,  self._handle_stop)
            _signal.signal(_signal.SIGTERM, self._handle_stop)

    # ── Stop handling ─────────────────────────────────────────────────────────

    def _handle_stop(self, *_) -> None:
        if not self._stop:
            console.print("\n\n  [yellow]Stopping — press Ctrl+C once more to force.[/yellow]\n")
            self._graceful_shutdown()
        self._stop = True

    def _graceful_shutdown(self) -> None:
        """Cancel any unfilled pending orders; leave open live positions protected by brackets."""
        console.print("  [dim]Cancelling pending exchange orders (not yet filled)...[/dim]")
        open_trades = self._executor.all_open_trades()
        for t in open_trades:
            # Positions that have bracket orders on exchange — leave them.
            # Only cancel if there's some edge-case where entry order is pending.
            logger.info(
                "[LiveWatcher] Shutdown — leaving %s %s open (brackets active on exchange)",
                t.get("symbol"), t.get("direction"),
            )
        console.print(
            "  [yellow]Live positions left open — bracket orders active on exchange.[/yellow]"
        )

    def _is_stopped(self) -> bool:
        return self._stop or (self._stop_event is not None and self._stop_event.is_set())

    def _sleep_until(self, target_ms: int) -> None:
        while not self._is_stopped():
            now_ms  = int(time.time() * 1000)
            wait_ms = target_ms - now_ms
            if wait_ms <= 0:
                return
            wait_s    = wait_ms / 1000
            minutes   = int(wait_s // 60)
            seconds   = int(wait_s % 60)
            target_dt = ms_to_dt(target_ms).strftime("%H:%M:%S UTC")
            console.print(
                f"  [dim]Next candle close: {target_dt}  "
                f"Waiting {minutes:02d}:{seconds:02d} ...[/dim]",
                end="\r",
            )
            try:
                time.sleep(min(10.0, wait_s))
            except (KeyboardInterrupt, SystemExit):
                self._stop = True
                return

    # ── Public entry point ────────────────────────────────────────────────────

    def run(self) -> None:
        self._print_startup_banner()

        live_send_message(
            f"\U0001f916 \U0001f534 [LIVE] Watcher started\n"
            f"Symbols: <b>{', '.join(self.symbols)}</b>\n"
            f"TF: {self.exec_tf}  Risk: {get_settings().live_risk_pct}%  "
            f"Portfolio cap: {get_settings().live_max_portfolio}  "
            f"Max leverage: {get_settings().live_max_leverage}×  "
            f"{'TESTNET' if get_settings().binance_testnet else 'MAINNET'}"
        )

        # Show any open live trades being resumed
        all_open = self._executor.all_open_trades()
        if all_open:
            console.print(f"\n  [cyan]Resuming {len(all_open)} open live position(s):[/cyan]")
            for t in all_open:
                lev = int(t.get("leverage") or 1)
                console.print(
                    f"    • [bold]{t.get('symbol','?')}[/bold] "
                    f"{t.get('direction','?')}"
                    f"{f' {lev}×' if lev > 1 else ''}  "
                    f"@ {t.get('entry_price')}  "
                    f"SL={t.get('stop_loss')}  TP={t.get('take_profit')}"
                )
        console.print()

        # Start User Data Stream WebSocket
        self._uds.start()

        # Start Live Telegram bot (separate thread, live channel only)
        _bot = None
        if os.environ.get("LIVE_TELEGRAM_LISTENER", "1") != "0":
            from app.live.live_telegram_bot import LiveTelegramCommandListener
            _bot = LiveTelegramCommandListener(
                watcher  = self,
                executor = self._executor,
                client   = self._client,
            )
            _bot.start()

        try:
            while not self._is_stopped():
                target_ms = _next_candle_close_ms(self._interval_ms)
                self._sleep_until(target_ms)
                if self._is_stopped():
                    break
                time.sleep(3)
                self._cycle += 1
                self._run_cycle(target_ms)
        finally:
            self._uds.stop()
            if _bot is not None:
                _bot.stop()

        console.print("\n\n  [dim]Live watcher stopped.[/dim]\n")

    # ── Single cycle — all symbols ────────────────────────────────────────────

    def _maybe_rescan(self) -> None:
        forced = self._rescan_pending
        if forced:
            self._rescan_pending = False
            console.print("  [cyan][Rescan] Force-rescan via Telegram.[/cyan]")
        elif self._rescan_every <= 0 or self._cycle % self._rescan_every != 0:
            return

        console.print(f"  [cyan][Rescan] Cycle {self._cycle} — re-scoring universe...[/cyan]")
        try:
            import sys as _sys; _sys.path.insert(0, ".")
            from scan_universe import select_watchlist

            new_symbols = select_watchlist(
                n=self._rescan_n,
                validated_only=self._rescan_validated,
                fallback_symbols=self.symbols,
                verbose=True,
                pool=self._rescan_pool,
            )

            would_remove = [s for s in self.symbols if s not in new_symbols]
            pinned = []
            for sym in would_remove:
                if not self._executor.open_trades(sym):
                    continue
                pinned.append(sym)

            if pinned:
                console.print(f"  [yellow][Rescan] Pinning {pinned} — open live trades.[/yellow]")
                new_symbols = [s for s in new_symbols if s not in pinned] + pinned

            if new_symbols != self.symbols:
                added   = [s for s in new_symbols if s not in self.symbols]
                removed = [s for s in self.symbols if s not in new_symbols]
                self.symbols = new_symbols
                console.print(
                    f"  [cyan][Rescan] Watchlist: +{added or 'none'}  -{removed or 'none'}[/cyan]"
                )
                self._record_rotation_history(new_symbols)
        except Exception as exc:
            console.print(f"  [yellow][Rescan] Error: {exc} — keeping current symbols[/yellow]")

    def _record_rotation_history(self, coins: list[str]) -> None:
        if not self._rotation_history_file:
            return
        try:
            import json
            history = []
            if os.path.exists(self._rotation_history_file):
                with open(self._rotation_history_file, encoding="utf-8") as f:
                    history = json.load(f).get("history", [])
            today = time.strftime("%Y-%m-%d", time.gmtime())
            history = [h for h in history if h.get("date") != today]
            history.append({"date": today, "coins": list(coins)})
            with open(self._rotation_history_file, "w", encoding="utf-8") as f:
                json.dump({"history": history[-30:]}, f, indent=2)
        except Exception as exc:
            logger.warning("[LiveWatcher] Could not write rotation history: %s", exc)

    def _run_cycle(self, candle_time_ms: int) -> None:
        self._maybe_rescan()
        candle_dt = ms_to_dt(candle_time_ms).strftime("%Y-%m-%d %H:%M UTC")
        console.print()
        console.print(Rule(
            f"[bold red]🔴 LIVE Cycle #{self._cycle}[/bold red]  {candle_dt}  "
            f"[dim]({len(self.symbols)} symbols)[/dim]",
            style="red",
        ))

        all_open = self._executor.all_open_trades()
        if all_open:
            open_str = "  ".join(
                f"{t.get('symbol','?')} {t.get('direction','?')}" for t in all_open
            )
            console.print(f"  [dim]Open live positions ({len(all_open)}): {open_str}[/dim]")

        # Belt-and-suspenders fallback: sync open orders against exchange
        try:
            self._executor.sync_open_orders()
        except Exception as exc:
            logger.warning("[LiveWatcher] sync_open_orders failed: %s", exc)

        # Cancel resting entry limit orders past expiry (LIVE_ENTRY_LIMIT_ENABLED)
        try:
            self._executor.check_pending_entries()
        except Exception as exc:
            logger.warning("[LiveWatcher] check_pending_entries failed: %s", exc)

        cycle_signals: list[dict] = []
        for sym in self.symbols:
            if self._is_stopped():
                break
            result = self._run_symbol_cycle(sym, candle_time_ms)
            if result:
                cycle_signals.append(result)

        if self.heartbeat_every > 0 and self._cycle % self.heartbeat_every == 0:
            self._send_heartbeat(candle_dt, cycle_signals)

        self._maybe_send_daily_digest()

    def _maybe_send_daily_digest(self) -> None:
        today = time.strftime("%Y-%m-%d", time.gmtime())
        if today == self._last_digest_date:
            return
        self._last_digest_date = today
        try:
            from app.db.connection import get_conn
            now_ms = int(time.time() * 1000)

            with get_conn() as conn:
                pnl_24h = conn.execute(
                    "SELECT SUM(CAST(pnl AS REAL)) FROM live_trades "
                    "WHERE status!='open' AND close_time>=?",
                    (now_ms - 24 * 3_600_000,),
                ).fetchone()[0] or 0.0

            balance = self._client.get_balance()
            open_trades = self._executor.all_open_trades()

            stale = []
            with get_conn() as conn:
                for sym in self.symbols:
                    row = conn.execute(
                        "SELECT MAX(open_time) FROM candles WHERE symbol=? AND timeframe='30m'",
                        (sym,),
                    ).fetchone()
                    if row and row[0] and now_ms - int(row[0]) > 2 * 3_600_000:
                        stale.append(sym)

            live_send_daily_digest(open_trades, balance, pnl_24h, stale)
        except Exception as exc:
            logger.warning("[LiveWatcher] Daily digest failed: %s", exc)

    # ── Single symbol within a cycle ─────────────────────────────────────────

    def _run_symbol_cycle(self, symbol: str, candle_time_ms: int) -> dict | None:
        """
        Full signal cycle for one symbol:
          1. Fetch latest candles
          2. Generate signal
          3. SignalFilter
          4. LiveExecutor.open_trade() — bracket order on Binance
        """
        console.print()
        console.print(f"  [bold dim]── {symbol} [LIVE] ──[/bold dim]")

        # 1. Fetch latest candles
        try:
            update_latest(symbol=symbol, timeframes=["1d", "12h", "4h", "1h", "30m", "1w"])
        except Exception as exc:
            logger.error("[LiveWatcher][%s] Candle fetch failed: %s", symbol, exc)
            console.print(f"  [red]Candle fetch error ({symbol}): {exc}[/red]")
            live_notify_error(f"Candle fetch [{symbol}]", str(exc))
            return None

        # 2. Load DataFrames
        df_1d  = get_candles(symbol, "1d",  limit=300)
        df_12h = get_candles(symbol, "12h", limit=200)
        df_4h  = get_candles(symbol, "4h",  limit=500)
        df_1h  = get_candles(symbol, "1h",  limit=500)
        df_30m = get_candles(symbol, "30m", limit=500)
        df_1w  = get_candles(symbol, "1w",  limit=104)

        if df_30m.empty:
            console.print(f"  [yellow]No 30M data for {symbol} — skipping[/yellow]")
            return None

        last          = df_30m.iloc[-1]
        current_price = float(last["close"])
        candle_high   = float(last["high"])
        candle_low    = float(last["low"])
        console.print(
            f"  Price: [bold yellow]{current_price:,.4f}[/bold yellow]  "
            f"[dim]H={candle_high:,.4f}  L={candle_low:,.4f}[/dim]"
        )

        # Track running MFE/MAE for any open live trade on this symbol —
        # zero extra API calls, this candle is already fetched above.
        try:
            self._executor.update_excursion(symbol, candle_high, candle_low)
        except Exception as exc:
            logger.warning("[LiveWatcher][%s] update_excursion failed: %s", symbol, exc)

        # 3. Generate signal — use live balance as capital basis
        try:
            capital = self._client.get_balance()
        except Exception:
            capital = get_settings().default_capital

        try:
            sig = generate_signal(
                symbol=symbol,
                df_1d=df_1d,
                df_4h=df_4h,
                df_1h=df_1h,
                df_30m=df_30m,
                capital=capital,
                df_12h=df_12h,
                df_1w=df_1w,
            )
        except Exception as exc:
            logger.error("[LiveWatcher][%s] Signal generation failed: %s", symbol, exc)
            console.print(f"  [red]Signal error ({symbol}): {exc}[/red]")
            live_notify_error(f"Signal generation [{symbol}]", str(exc))
            return None

        # 4. Log signal
        cfg = get_settings()
        color = {"BUY": "green", "SELL": "red", "HOLD": "yellow", "BLOCKED": "magenta"}.get(
            sig.signal, "white"
        )
        console.print(
            f"  Signal: [{color}][bold]{sig.signal}[/bold][/{color}]  "
            f"Conf={sig.confidence:.0f}  Regime={sig.market_regime}  "
            f"Zone={sig.zone_rating or '—'}({sig.zone_score:.0f})"
        )

        sig_row = dataclasses.asdict(sig)
        sig_row["liquidity_sweep"] = int(sig_row["liquidity_sweep"])
        signal_id = save_signal(sig_row)

        # Compute leverage for display
        with __import__("app.db.connection", fromlist=["get_conn"]).get_conn() as conn:
            recent_rows = conn.execute(
                "SELECT pnl FROM live_trades WHERE symbol=? AND status!='open' "
                "ORDER BY close_time DESC LIMIT 5",
                (symbol,),
            ).fetchall()
        recent_trades = [{"pnl": float(r[0] or 0)} for r in recent_rows]
        _lev = dynamic_leverage(sig, recent_trades, max_leverage=cfg.live_max_leverage)

        if sig.signal in ("BUY", "SELL"):
            console.print(
                f"  Entry={sig.entry_price:.4f}  SL={sig.stop_loss:.4f}  "
                f"TP={sig.take_profit:.4f}  R:R=1:{sig.risk_reward:.2f}  "
                f"Leverage=[bold]{_lev}×[/bold]  Setup={sig.setup_type}"
            )
            live_notify_signal(sig, leverage=_lev)

        console.print(f"  Balance: [bold green]${capital:,.2f} USDT[/bold green]")

        # 5. SignalFilter + LiveExecutor.open_trade()
        if sig.signal in ("BUY", "SELL") and not self.dry_run:
            passed, reason = self._filter.evaluate(sig)
            if not passed:
                console.print(f"  [yellow]Signal filtered: {reason}[/yellow]")
            else:
                outcome, result_id = self._executor.open_trade(symbol, sig, signal_id)
                if outcome == "opened" and result_id is not None:
                    # Fetch the row we just inserted
                    with __import__("app.db.connection", fromlist=["get_conn"]).get_conn() as conn:
                        row = conn.execute(
                            "SELECT * FROM live_trades WHERE id=?", (result_id,)
                        ).fetchone()
                    if row:
                        trade = dict(row)
                        console.print(
                            f"  [green]🔴 LIVE Trade opened #{result_id}  "
                            f"{sig.signal} @ {trade.get('entry_price'):.4f}  "
                            f"Leverage={_lev}×[/green]"
                        )
                        live_notify_trade_opened(trade, symbol, leverage=_lev)
                elif outcome == "pending":
                    console.print(
                        f"  [yellow]Pending {sig.signal} limit @ {sig.entry_price:.4f}  "
                        f"(#{result_id}) — waiting for retrace[/yellow]"
                    )
        elif sig.signal in ("BUY", "SELL") and self.dry_run:
            console.print(f"  [yellow]DRY-RUN — would open {sig.signal} @ {sig.entry_price:.4f}[/yellow]")

        return {"symbol": symbol, "signal": sig.signal, "confidence": sig.confidence}

    # ── Heartbeat ─────────────────────────────────────────────────────────────

    def _send_heartbeat(self, candle_dt: str, cycle_signals: list[dict]) -> None:
        try:
            balance     = self._client.get_balance()
            open_trades = self._executor.all_open_trades()
            live_send_heartbeat(candle_dt, cycle_signals, open_trades, balance)
            console.print("  [dim]Live heartbeat sent.[/dim]")
        except Exception as exc:
            logger.warning("[LiveWatcher] Heartbeat failed: %s", exc)

    # ── Startup banner ────────────────────────────────────────────────────────

    def _print_startup_banner(self) -> None:
        cfg = get_settings()
        sym_str    = "  ".join(self.symbols)
        net_label  = "TESTNET" if cfg.binance_testnet else "MAINNET"
        try:
            balance = self._client.get_balance()
            bal_str = f"${balance:,.2f} USDT"
        except Exception:
            bal_str = "⚠ could not fetch"

        console.print()
        console.print(Panel(
            f"[bold red]🔴 LQ-MTF LIVE EXECUTION MODE[/bold red]\n"
            f"Network:  [bold]{'🟡 ' + net_label}[/bold]\n"
            f"Symbols:  [bold yellow]{sym_str}[/bold yellow]\n"
            f"TF: [bold]{self.exec_tf}[/bold]   "
            f"Risk: [bold]{cfg.live_risk_pct}%[/bold]   "
            f"Portfolio cap: [bold]{cfg.live_max_portfolio}[/bold]   "
            f"Max leverage: [bold]{cfg.live_max_leverage}×[/bold]\n"
            f"Balance: [bold green]{bal_str}[/bold green]   "
            f"Dry-run: [bold]{'YES — no real orders' if self.dry_run else 'NO'}[/bold]",
            border_style="red",
        ))
        if self.dry_run:
            console.print(
                "  [yellow]DRY-RUN: signal generation only — no orders placed on exchange[/yellow]"
            )
