"""
LQ-MTF Live Watcher — real-time signal generation and paper trading.

Architecture:
  - 30-m signal loop   : sleeps to candle close, generates signals, opens trades
  - PriceMonitor thread : polls ticker every 60 s, detects intrabar SL/TP
  - PositionManager     : single owner of all trade state transitions
  - SignalFilter        : pre-trade quality gate (zone, bias, confidence, R:R)

Run:
    python main.py live --symbol BTCUSDT
    python main.py live --symbol BTCUSDT --symbol ETHUSDT --symbol SOLUSDT
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

from app.data.backfill import update_latest
from app.data.repository import (
    get_candles,
    get_latest_signal,
    save_signal,
)
from app.notifications.telegram import (
    notify_error,
    notify_trade_closed,
    notify_trade_opened,
    notify_signal,
    send_message,
)
from app.paper.account import get_paper_capital, get_portfolio_capital, set_paper_capital
from app.paper.position_manager import PositionManager
from app.paper.signal_filter import SignalFilter
from app.live.price_monitor import PriceMonitor
from app.ta.signals import generate_signal, SignalResult
from app.utils.logger import get_logger
from app.utils.timeframes import ms_to_dt, tf_to_ms

logger  = get_logger(__name__)
console = Console()

_DANGER_REGIMES = {"choppy", "high_volatility_liquidation"}


# ── Timing ────────────────────────────────────────────────────────────────────

def _next_candle_close_ms(interval_ms: int) -> int:
    """Return Unix ms of the next candle close for a given interval."""
    now_ms = int(time.time() * 1000)
    return ((now_ms // interval_ms) + 1) * interval_ms


# ── Main watcher ──────────────────────────────────────────────────────────────

class LiveWatcher:
    """
    Runs the strategy in a continuous loop aligned to candle closes.

    The 30-m loop handles signal generation and trade opening.
    The PriceMonitor background thread handles intrabar SL/TP detection.
    All trade state goes through PositionManager.

    Parameters
    ----------
    symbols          : Trading pairs, e.g. ["BTCUSDT", "ETHUSDT"]
    exec_timeframe   : Execution timeframe (default "30m")
    heartbeat_every  : Telegram heartbeat every N cycles (0 = never)
    paper_capital    : Starting capital applied to every symbol on first run
    risk_pct         : Risk per trade as % of capital
    dry_run          : Generate signals but never open trades
    stop_event       : External threading.Event to stop the watcher
    rescan_every     : Re-run universe scanner every N cycles (0 = disabled)
    rescan_n         : Symbols to pick on rescan
    rescan_validated : Only include validated symbols on rescan
    """

    def __init__(
        self,
        symbols:          list[str],
        exec_timeframe:   str   = "30m",
        heartbeat_every:  int   = 8,
        paper_capital:    float | None = None,
        risk_pct:         float = 1.0,
        dry_run:          bool  = False,
        stop_event:       threading.Event | None = None,
        rescan_every:     int   = 0,
        rescan_n:         int   = 0,
        rescan_validated: bool  = True,
        sync:             bool  = False,
        sync_top:         int   = 50,
        sync_min_volume:  float = 50_000_000,
        telegram_listener: bool = True,
        rescan_pool:      list[str] | None = None,
        rotation_history_file: str | None = None,
    ) -> None:
        self.symbols           = [s.upper() for s in symbols]
        self.exec_tf           = exec_timeframe
        self.heartbeat_every   = heartbeat_every
        self.risk_pct          = risk_pct
        self.dry_run           = dry_run
        self._stop             = False
        self._stop_event       = stop_event
        self._cycle            = 0
        self._interval_ms      = tf_to_ms(exec_timeframe)
        self._rescan_every     = rescan_every
        self._rescan_n         = rescan_n
        self._rescan_validated = rescan_validated
        self._rescan_pending   = False
        self._sync             = sync
        self._sync_top         = sync_top
        self._sync_min_volume  = sync_min_volume
        self._telegram_listener = telegram_listener
        self._rescan_pool      = [s.upper() for s in rescan_pool] if rescan_pool else None
        self._rotation_history_file = rotation_history_file
        self._last_digest_date = time.strftime("%Y-%m-%d", time.gmtime())

        if paper_capital is not None:
            for sym in self.symbols:
                set_paper_capital(sym, paper_capital)

        # Core components
        self._pm     = PositionManager(self.symbols, risk_pct=risk_pct)
        self._filter = SignalFilter()
        self._monitor = PriceMonitor(
            symbols          = self.symbols,
            position_manager = self._pm,
            on_trade_closed  = self._on_monitor_close,
            on_order_filled  = self._on_monitor_fill,
        )

        if stop_event is None:
            _signal.signal(_signal.SIGINT,  self._handle_stop)
            _signal.signal(_signal.SIGTERM, self._handle_stop)

    # ── Backward compat ───────────────────────────────────────────────────────

    @property
    def symbol(self) -> str:
        return self.symbols[0]

    # ── Stop handling ─────────────────────────────────────────────────────────

    def _handle_stop(self, *_) -> None:
        if not self._stop:
            console.print("\n\n  [yellow]Stopping — press Ctrl+C once more to force quit.[/yellow]\n")
        self._stop = True

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

    # ── Callback from PriceMonitor ────────────────────────────────────────────

    def _on_monitor_close(self, trade: dict, symbol: str) -> None:
        """
        Called by PriceMonitor when it closes a trade between candle closes.
        Updates capital and sends Telegram notification.
        """
        pnl    = trade.get("pnl", 0)
        status = trade.get("status", "closed")
        color  = "green" if pnl >= 0 else "red"
        # Capital already updated by PositionManager._evaluate — do not update again here
        console.print(
            f"  [{color}][PriceMonitor] Trade CLOSED ({status.replace('_', ' ')}) "
            f"{symbol}  PnL={'+'if pnl>=0 else ''}{pnl:.2f}[/{color}]"
        )
        lev = int(trade.get("leverage") or 1)
        notify_trade_closed(trade, symbol, leverage=lev)

    def _on_monitor_fill(self, order: dict, trade_id: int, symbol: str) -> None:
        """Called by PriceMonitor when a pending limit order fills intrabar."""
        console.print(
            f"  [green][PriceMonitor] Limit order FILLED  {symbol} "
            f"{order.get('direction')} @ {order.get('limit_price')}[/green]"
        )
        notify_trade_opened(
            {
                "direction":     order.get("direction"),
                "entry_price":   order.get("limit_price"),
                "stop_loss":     order.get("stop_loss"),
                "take_profit":   order.get("take_profit"),
                "risk_reward":   order.get("risk_reward"),
                "position_size": 0.0,
            },
            symbol,
            leverage=1,
        )

    # ── Public entry point ────────────────────────────────────────────────────

    def run(self) -> None:
        self._print_startup_banner()

        telegram_ok = self._check_telegram()
        console.print(
            f"  Telegram: [{'green' if telegram_ok else 'red'}]"
            f"{'Connected' if telegram_ok else 'Not configured / offline'}[/{'green' if telegram_ok else 'red'}]"
        )

        # Show any open trades being resumed
        all_open = self._pm.all_open_trades()
        if all_open:
            console.print(f"\n  [cyan]Resuming {len(all_open)} open position(s):[/cyan]")
            for t in all_open:
                lev  = int(t.get("leverage") or 1)
                upnl = float(t.get("pnl") or 0)
                console.print(
                    f"    • [bold]{t.get('symbol','?')}[/bold] "
                    f"{t.get('direction','?')}"
                    f"{f' {lev}×' if lev > 1 else ''}  "
                    f"@ {t.get('entry_price')}  "
                    f"SL={t.get('stop_loss')}  TP={t.get('take_profit')}  "
                    f"uPnL=[{'green' if upnl >= 0 else 'red'}]"
                    f"{'+'if upnl>=0 else ''}{upnl:.2f}[/{'green' if upnl >= 0 else 'red'}]"
                )
        console.print()

        # Start background threads
        self._monitor.start()
        # Only one listener may long-poll Telegram getUpdates per bot token —
        # concurrent pollers get HTTP 409. run_live_paper.py sets
        # TELEGRAM_LISTENER=0 for all but one child process; run_live_rotation.py
        # passes telegram_listener=False for all but one watcher thread.
        _bot = None
        if self._telegram_listener and os.environ.get("TELEGRAM_LISTENER", "1") != "0":
            from app.notifications.telegram_bot import TelegramCommandListener
            _bot = TelegramCommandListener(self)
            _bot.start()

        try:
            while not self._is_stopped():
                target_ms = _next_candle_close_ms(self._interval_ms)
                self._sleep_until(target_ms)
                if self._is_stopped():
                    break
                time.sleep(3)   # brief buffer so the new candle is available
                self._cycle += 1
                self._run_cycle(target_ms)
        finally:
            self._monitor.stop()
            if _bot is not None:
                _bot.stop()

        console.print("\n\n  [dim]Live watcher stopped.[/dim]\n")

    # ── Single cycle — all symbols ────────────────────────────────────────────

    def _maybe_rescan(self) -> None:
        """Re-run the universe scanner and update self.symbols if triggered."""
        forced = self._rescan_pending
        if forced:
            self._rescan_pending = False
            console.print("  [cyan][Rescan] Force-rescan via Telegram.[/cyan]")
        elif self._rescan_every <= 0 or self._cycle % self._rescan_every != 0:
            return

        console.print(f"  [cyan][Rescan] Cycle {self._cycle} — re-scoring universe...[/cyan]")
        try:
            import sys as _sys; _sys.path.insert(0, ".")
            from scan_universe import select_watchlist, auto_backtest_new_symbols

            # If the watcher was started with --sync, re-sync and kick off
            # auto-backtests for any new coins in the background.
            # Backtests run in a daemon thread so the rescan + candle loop
            # continue unblocked. New symbols join the watchlist on the
            # *next* rescan once their backtests complete.
            if self._sync:
                import threading as _t
                from app.data.universe import sync_universe
                console.print("  [cyan][Rescan] Syncing universe...[/cyan]")
                sync_universe(n=self._sync_top, min_volume_usd=self._sync_min_volume)
                _t.Thread(
                    target=auto_backtest_new_symbols,
                    name="AutoBacktest",
                    daemon=True,
                ).start()
                console.print("  [cyan][Rescan] Auto-backtest started in background.[/cyan]")

            new_symbols = select_watchlist(
                n=self._rescan_n,
                validated_only=self._rescan_validated,
                fallback_symbols=self.symbols,
                verbose=True,
                pool=self._rescan_pool,
            )
            if new_symbols == self.symbols:
                console.print("  [dim][Rescan] Watchlist unchanged.[/dim]")
                return

            would_remove = [s for s in self.symbols if s not in new_symbols]
            pinned = []
            for sym in would_remove:
                if not self._pm.open_trades(sym):
                    continue
                latest = get_latest_signal(sym)
                regime = (latest or {}).get("market_regime", "")
                if regime in _DANGER_REGIMES:
                    console.print(
                        f"  [red][Rescan] Emergency close {sym} (regime={regime})[/red]"
                    )
                    self._pm.emergency_close(sym, regime)
                    closed = self._pm.all_open_trades()   # re-check
                    pnl_sum = sum(t.get("pnl", 0) for t in closed)
                    send_message(
                        f"⚠️ <b>Emergency Rotation — {sym}</b>\n"
                        f"Regime: <b>{regime}</b> — trade force-closed at market."
                    )
                else:
                    pinned.append(sym)

            if pinned:
                console.print(f"  [yellow][Rescan] Pinning {pinned} — open trades in progress.[/yellow]")
                new_symbols = [s for s in new_symbols if s not in pinned] + pinned

            added   = [s for s in new_symbols if s not in self.symbols]
            removed = [s for s in self.symbols if s not in new_symbols]
            self.symbols = new_symbols
            self._pm.symbols = new_symbols
            self._monitor.update_symbols(new_symbols)

            if added or removed:
                console.print(
                    f"  [cyan][Rescan] Watchlist: +{added or 'none'}  -{removed or 'none'}[/cyan]"
                )
                self._record_rotation_history(new_symbols)
        except Exception as exc:
            console.print(f"  [yellow][Rescan] Error: {exc} — keeping current symbols[/yellow]")

    def _record_rotation_history(self, coins: list[str]) -> None:
        """Append/refresh today's watchlist in rotation_history.json (audit only)."""
        if not self._rotation_history_file:
            return
        try:
            import json
            import os
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
            logger.warning("[Live] Could not write rotation history: %s", exc)

    def _run_cycle(self, candle_time_ms: int) -> None:
        self._maybe_rescan()
        candle_dt = ms_to_dt(candle_time_ms).strftime("%Y-%m-%d %H:%M UTC")
        console.print()
        console.print(Rule(
            f"[bold cyan]Cycle #{self._cycle}[/bold cyan]  {candle_dt}  "
            f"[dim]({len(self.symbols)} symbols)[/dim]",
            style="cyan",
        ))

        all_open = self._pm.all_open_trades()
        if all_open:
            open_str = "  ".join(
                f"{t.get('symbol','?')} {t.get('direction','?')}" for t in all_open
            )
            console.print(f"  [dim]Open positions ({len(all_open)}): {open_str}[/dim]")

        cycle_signals: list[tuple[str, str, float]] = []
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
        """Once per UTC day: equity, 24h realized PnL, position aging, data freshness."""
        today = time.strftime("%Y-%m-%d", time.gmtime())
        if today == self._last_digest_date:
            return
        self._last_digest_date = today
        try:
            from app.data.repository import get_closed_pnl_since
            now_ms  = int(time.time() * 1000)
            pnl_24h = get_closed_pnl_since(now_ms - 24 * 3_600_000)
            equity  = get_portfolio_capital()

            lines = [
                f"\U0001f4c5 <b>Daily Digest</b>  {today}",
                f"Equity: <b>${equity:,.2f}</b>",
                f"Realized 24h: {'+' if pnl_24h >= 0 else ''}{pnl_24h:,.2f}",
            ]
            open_trades = self._pm.all_open_trades()
            if open_trades:
                lines.append(f"Open positions: {len(open_trades)}")
                for t in open_trades:
                    age_h = (now_ms - int(t.get("open_time") or now_ms)) / 3_600_000
                    upnl = float(t.get("pnl") or 0)
                    lines.append(
                        f"  • {t.get('symbol','?')} {t.get('direction','?')} "
                        f"({age_h:.0f}h)  uPnL {'+' if upnl >= 0 else ''}{upnl:.2f}"
                    )
            pending = [o for s in self.symbols for o in self._pm.pending_orders(s)]
            if pending:
                lines.append(f"Pending limit orders: {len(pending)}")

            # Data freshness per watched symbol
            stale = []
            from app.db.connection import get_conn
            with get_conn() as conn:
                for sym in self.symbols:
                    row = conn.execute(
                        "SELECT MAX(open_time) FROM candles WHERE symbol=? AND timeframe='30m'",
                        (sym,),
                    ).fetchone()
                    if row and row[0] and now_ms - int(row[0]) > 2 * 3_600_000:
                        stale.append(sym)
            if stale:
                lines.append(f"⚠ Stale 30m candles: {', '.join(stale)}")

            send_message("\n".join(lines))
        except Exception as exc:
            logger.warning("[Live] Daily digest failed: %s", exc)

    # ── Single symbol within a cycle ─────────────────────────────────────────

    def _run_symbol_cycle(
        self,
        symbol: str,
        candle_time_ms: int,
    ) -> tuple[str, str, float] | None:
        """
        Full signal cycle for one symbol:
          1. Fetch latest candles
          2. Check open trades via candle high/low (intrabar-aware)
          3. Generate signal
          4. Apply SignalFilter
          5. Open trade via PositionManager
        """
        console.print()
        console.print(f"  [bold dim]── {symbol} ──[/bold dim]")

        # 1. Fetch latest candles
        try:
            update_latest(symbol=symbol, timeframes=["1d", "12h", "4h", "1h", "30m", "1w"])
        except Exception as exc:
            logger.error("[Live][%s] Candle fetch failed: %s", symbol, exc)
            console.print(f"  [red]Candle fetch error ({symbol}): {exc}[/red]")
            notify_error(f"Candle fetch [{symbol}]", str(exc))
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

        # Current candle's OHLC for intrabar-aware SL/TP check
        last = df_30m.iloc[-1]
        current_price = float(last["close"])
        candle_high   = float(last["high"])
        candle_low    = float(last["low"])
        console.print(f"  Price: [bold yellow]{current_price:,.4f}[/bold yellow]  "
                      f"[dim]H={candle_high:,.4f}  L={candle_low:,.4f}[/dim]")

        # 3a. Fill/expire pending limit orders against the candle range
        for ev in self._pm.check_pending(symbol, candle_high, candle_low):
            if ev["outcome"] == "filled":
                self._on_monitor_fill(ev["order"], ev["trade_id"], symbol)
            else:
                console.print(
                    f"  [yellow]Pending order #{ev['order']['id']} "
                    f"{ev['outcome']}[/yellow]"
                )

        # 3b. Check open trades using candle high/low (intrabar SL/TP)
        closed = self._pm.check_candle(symbol, candle_high, candle_low, current_price)
        for t in closed:
            pnl    = t.get("pnl", 0)
            status = t.get("status", "closed")
            color  = "green" if pnl >= 0 else "red"
            capital = get_paper_capital(symbol)
            console.print(
                f"  [{color}]Trade CLOSED ({status.replace('_', ' ')}) "
                f"PnL={'+'if pnl>=0 else ''}{pnl:.2f}  "
                f"Capital=${capital:,.2f}[/{color}]"
            )
            notify_trade_closed(t, symbol, leverage=int(t.get("leverage") or 1))

        # 4. Generate signal
        capital = get_paper_capital(symbol)
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
            logger.error("[Live][%s] Signal generation failed: %s", symbol, exc)
            console.print(f"  [red]Signal error ({symbol}): {exc}[/red]")
            notify_error(f"Signal generation [{symbol}]", str(exc))
            return None

        # 5. Print and save signal
        self._print_signal_summary(symbol, sig, capital)

        sig_row = dataclasses.asdict(sig)
        sig_row["liquidity_sweep"] = int(sig_row["liquidity_sweep"])
        signal_id = save_signal(sig_row)

        # 6. Telegram for actionable signals
        if sig.signal in ("BUY", "SELL"):
            from app.ta.leverage import dynamic_leverage
            from app.data.repository import get_recent_closed_paper_trades
            from app.config import get_settings
            _lev = dynamic_leverage(
                sig,
                recent_trades=get_recent_closed_paper_trades(symbol, 5),
                max_leverage=get_settings().max_leverage,
            )
            notify_signal(sig, leverage=_lev)

        # 7. SignalFilter + submit (limit-or-market entry)
        if sig.signal in ("BUY", "SELL") and not self.dry_run:
            passed, reason = self._filter.evaluate(sig)
            if not passed:
                console.print(f"  [yellow]Signal filtered: {reason}[/yellow]")
            else:
                outcome, oid = self._pm.submit(
                    symbol, sig, capital, signal_id, current_price=current_price
                )
                if outcome == "pending":
                    console.print(
                        f"  [cyan]Limit order #{oid} placed  "
                        f"{sig.signal} @ {sig.entry_price:.4f} "
                        f"(price now {current_price:.4f})[/cyan]"
                    )
                trade_id = oid if outcome == "opened" else None
                if trade_id:
                    opened = self._pm.open_trades(symbol)
                    pos_size = float(opened[0]["position_size"]) if opened else 0.0
                    from app.ta.leverage import dynamic_leverage
                    from app.data.repository import get_recent_closed_paper_trades
                    from app.config import get_settings
                    _lev = dynamic_leverage(
                        sig,
                        recent_trades=get_recent_closed_paper_trades(symbol, 5),
                        max_leverage=get_settings().max_leverage,
                    )
                    notify_trade_opened(
                        {
                            "direction":     sig.signal,
                            "entry_price":   sig.entry_price,
                            "stop_loss":     sig.stop_loss,
                            "take_profit":   sig.take_profit,
                            "risk_reward":   sig.risk_reward,
                            "position_size": pos_size,
                        },
                        symbol,
                        leverage=_lev,
                    )
                    console.print(
                        f"  [green]Trade opened #{trade_id}  "
                        f"{sig.signal} @ {sig.entry_price:.4f}  "
                        f"Leverage={_lev}×[/green]"
                    )

        return (symbol, sig.signal, sig.confidence)

    # ── Display helpers ───────────────────────────────────────────────────────

    def _print_startup_banner(self) -> None:
        sym_str = "  ".join(self.symbols)
        console.print()
        console.print(Panel(
            f"[bold cyan]LQ-MTF Multi-Symbol Watcher[/bold cyan]\n"
            f"Symbols: [bold yellow]{sym_str}[/bold yellow]\n"
            f"Timeframe: [bold]{self.exec_tf}[/bold]   "
            f"Risk: [bold]{self.risk_pct}%[/bold]   "
            f"Portfolio cap: [bold]{self._pm.max_portfolio}[/bold]   "
            f"Corr cap: [bold]{self._pm.max_same_dir}[/bold]/direction   "
            f"Dry-run: [bold]{'YES' if self.dry_run else 'NO'}[/bold]",
            border_style="cyan",
        ))
        if self.dry_run:
            console.print("  [yellow]DRY-RUN mode: signals generated but NO trades opened[/yellow]")

    def _print_signal_summary(self, symbol: str, sig: SignalResult, capital: float) -> None:
        color = {"BUY": "green", "SELL": "red", "HOLD": "yellow", "BLOCKED": "magenta"}.get(
            sig.signal, "white"
        )
        console.print(
            f"  Signal: [{color}][bold]{sig.signal}[/bold][/{color}]  "
            f"Conf={sig.confidence:.0f}  "
            f"Regime={sig.market_regime}  "
            f"Zone={sig.zone_rating or '—'}({sig.zone_score:.0f})"
        )
        if sig.long_score or sig.short_score:
            console.print(
                f"  MTF: LONG={sig.long_score:.1f}  SHORT={sig.short_score:.1f}"
                + (f"  [{color}]{sig.rejection_reason}[/{color}]" if sig.rejection_reason else "")
            )
        if sig.signal in ("BUY", "SELL"):
            from app.ta.leverage import dynamic_leverage
            from app.data.repository import get_recent_closed_paper_trades
            from app.config import get_settings
            _lev = dynamic_leverage(
                sig,
                recent_trades=get_recent_closed_paper_trades(symbol, 5),
                max_leverage=get_settings().max_leverage,
            )
            console.print(
                f"  Entry={sig.entry_price:.4f}  "
                f"SL={sig.stop_loss:.4f}  "
                f"TP={sig.take_profit:.4f}  "
                f"R:R=1:{sig.risk_reward:.2f}  "
                f"Leverage={'[bold]' + str(_lev) + '×[/bold]' if _lev > 1 else '1×'}  "
                f"Setup={sig.setup_type}"
            )
        console.print(
            f"  Equity: [bold green]${get_portfolio_capital():,.2f}[/bold green]  "
            f"[dim]({symbol} ledger: ${capital:,.2f})[/dim]"
        )

    def _send_heartbeat(
        self,
        candle_dt: str,
        cycle_signals: list[tuple[str, str, float]],
    ) -> None:
        lines = [f"\U0001f493 <b>Heartbeat</b>  {candle_dt}"]
        for sym, sig_type, conf in cycle_signals:
            emoji = {"BUY": "\U0001f7e2", "SELL": "\U0001f534"}.get(sig_type, "⚪")
            lines.append(f"{emoji} <b>{sym}</b>: {sig_type} (conf={conf:.0f})")

        all_open = self._pm.all_open_trades()
        if all_open:
            lines.append("")
            lines.append(f"\U0001f4bc Open: {len(all_open)}/{self._pm.max_portfolio}")
            for t in all_open:
                upnl     = float(t.get("pnl") or 0)
                upnl_pct = float(t.get("pnl_pct") or 0)
                entry    = float(t.get("entry_price") or 0)
                lev      = int(t.get("leverage") or 1)
                lines.append(
                    f"  • {t.get('symbol','?')} {t.get('direction','?')}"
                    f"{f' {lev}×' if lev > 1 else ''}  "
                    f"@ <code>{entry:.4f}</code>  "
                    f"uPnL: {'+'if upnl>=0 else ''}{upnl:.2f} ({upnl_pct:+.2f}%)"
                )

        if send_message("\n".join(lines)):
            console.print("  [dim]Heartbeat sent.[/dim]")

    def _check_telegram(self) -> bool:
        return send_message(
            f"\U0001f916 <b>LQ-MTF Watcher started</b>\n"
            f"Symbols: <b>{', '.join(self.symbols)}</b>\n"
            f"TF: {self.exec_tf}  Risk: {self.risk_pct}%  "
            f"Portfolio cap: {self._pm.max_portfolio}  "
            f"Corr cap: {self._pm.max_same_dir}/direction"
        )
