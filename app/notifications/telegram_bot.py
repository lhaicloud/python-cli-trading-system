"""
Interactive Telegram bot listener.

Runs a long-polling getUpdates loop in a background daemon thread so the
watcher can receive commands while it continues running its main cycle loop.

Activate by instantiating TelegramCommandListener(watcher) and calling
.start() / .stop().  Responds only to the configured TELEGRAM_CHAT_ID.

Available buttons (sent via /menu or the ☰ Menu button):
  Row 1 — Positions : 📊 Open Trades | 📉 Last Closed | ⚠️ Risk Stats
  Row 2 — Intel     : 📈 Latest Signals | 🌍 Watchlist | 🧠 ML Models
  Row 3 — Control   : 🛑 Pause Trading | ▶️ Resume | 🔁 Force Rescan
  Row 4 — Account   : 💰 Capital | ❤️ Status | 📋 Summary
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from app.config import get_settings
from app.utils.logger import get_logger

if TYPE_CHECKING:
    from app.live.watcher import LiveWatcher

logger = get_logger(__name__)

_BASE = "https://api.telegram.org"

# ── Inline keyboard layout ────────────────────────────────────────────────────

_MENU_KEYBOARD = {
    "inline_keyboard": [
        [
            {"text": "📊 Open Trades",    "callback_data": "open_trades"},
            {"text": "📉 Last Closed",    "callback_data": "last_closed"},
            {"text": "⚠️ Risk Stats",     "callback_data": "risk_stats"},
        ],
        [
            {"text": "📈 Latest Signals", "callback_data": "latest_signals"},
            {"text": "🌍 Watchlist",      "callback_data": "watchlist"},
            {"text": "🧠 ML Models",      "callback_data": "ml_models"},
        ],
        [
            {"text": "🛑 Pause Trading",  "callback_data": "pause"},
            {"text": "▶️ Resume",          "callback_data": "resume"},
            {"text": "🔁 Force Rescan",   "callback_data": "force_rescan"},
        ],
        [
            {"text": "💰 Capital",        "callback_data": "capital"},
            {"text": "❤️ Status",         "callback_data": "status"},
            {"text": "📋 Summary",        "callback_data": "summary"},
        ],
        [
            {"text": "🪙 Coin Summary",   "callback_data": "coin_summary"},
        ],
    ]
}


class TelegramCommandListener:
    """
    Background thread that polls Telegram for incoming messages/button taps
    and dispatches them to the appropriate handler.

    Parameters
    ----------
    watcher : LiveWatcher
        Reference to the running watcher instance — used to read live state
        and set the dry_run / rescan_pending flags.
    """

    def __init__(self, watcher: LiveWatcher) -> None:
        self._watcher = watcher
        self._thread: threading.Thread | None = None
        self._stop_flag = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._stop_flag = False
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="telegram-bot",
            daemon=True,
        )
        self._thread.start()
        logger.info("[TelegramBot] Listener started")

    def stop(self) -> None:
        self._stop_flag = True

    # ── Long-polling loop ─────────────────────────────────────────────────────

    def _poll_loop(self) -> None:
        cfg   = get_settings()
        token = getattr(cfg, "telegram_bot_token", "")
        if not token:
            logger.debug("[TelegramBot] No token configured — listener inactive")
            return

        offset = 0
        while not self._stop_flag:
            try:
                updates = self._get_updates(token, offset, timeout=20)
                for upd in updates:
                    offset = upd["update_id"] + 1
                    self._dispatch(upd)
            except Exception as exc:
                logger.debug("[TelegramBot] Poll error: %s", exc)
                time.sleep(5)

    def _get_updates(self, token: str, offset: int, timeout: int = 20) -> list[dict]:
        url = f"{_BASE}/bot{token}/getUpdates"
        try:
            resp = httpx.get(
                url,
                params={"offset": offset, "timeout": timeout, "allowed_updates": ["message", "callback_query"]},
                timeout=timeout + 5,
            )
            data = resp.json()
            return data.get("result", []) if data.get("ok") else []
        except Exception:
            return []

    # ── Dispatch ──────────────────────────────────────────────────────────────

    def _dispatch(self, update: dict) -> None:
        cfg     = get_settings()
        chat_id = str(getattr(cfg, "telegram_chat_id", ""))

        if "message" in update:
            msg     = update["message"]
            from_id = str(msg.get("chat", {}).get("id", ""))
            if from_id != chat_id:
                return
            text = msg.get("text", "").strip()
            if text.startswith("/menu") or text.lower() in ("menu", "help"):
                self._send_menu(chat_id)
            elif text.startswith("/"):
                # Unknown command — show menu
                self._send_menu(chat_id)

        elif "callback_query" in update:
            cb      = update["callback_query"]
            from_id = str(cb.get("message", {}).get("chat", {}).get("id", ""))
            if from_id != chat_id:
                return
            cq_id = cb.get("id", "")
            data  = cb.get("data", "")
            self._answer_callback(cq_id)
            self._handle_callback(chat_id, data)

    # ── Low-level API helpers ─────────────────────────────────────────────────

    def _api(self, method: str, payload: dict) -> bool:
        cfg   = get_settings()
        token = getattr(cfg, "telegram_bot_token", "")
        if not token:
            return False
        try:
            resp = httpx.post(f"{_BASE}/bot{token}/{method}", json=payload, timeout=10)
            return resp.status_code == 200
        except Exception as exc:
            logger.debug("[TelegramBot] API %s failed: %s", method, exc)
            return False

    def _send(self, chat_id: str, text: str) -> bool:
        return self._api("sendMessage", {
            "chat_id":    chat_id,
            "text":       text,
            "parse_mode": "HTML",
        })

    def _send_menu(self, chat_id: str) -> bool:
        return self._api("sendMessage", {
            "chat_id":      chat_id,
            "text":         "🤖 <b>LQ-MTF Control Panel</b>\n\nSelect an option:",
            "parse_mode":   "HTML",
            "reply_markup": _MENU_KEYBOARD,
        })

    def _answer_callback(self, callback_query_id: str) -> None:
        self._api("answerCallbackQuery", {"callback_query_id": callback_query_id})

    # ── Button handlers ───────────────────────────────────────────────────────

    def _handle_callback(self, chat_id: str, data: str) -> None:
        handlers = {
            "open_trades":    self._cmd_open_trades,
            "last_closed":    self._cmd_last_closed,
            "risk_stats":     self._cmd_risk_stats,
            "latest_signals": self._cmd_latest_signals,
            "watchlist":      self._cmd_watchlist,
            "ml_models":      self._cmd_ml_models,
            "pause":          self._cmd_pause,
            "resume":         self._cmd_resume,
            "force_rescan":   self._cmd_force_rescan,
            "capital":        self._cmd_capital,
            "status":         self._cmd_status,
            "summary":        self._cmd_summary,
            "coin_summary":   self._cmd_coin_summary,
        }
        fn = handlers.get(data)
        if fn:
            try:
                fn(chat_id)
            except Exception as exc:
                logger.error("[TelegramBot] Handler %s failed: %s", data, exc)
                self._send(chat_id, f"⚠️ Error running <b>{data}</b>:\n<code>{str(exc)[:200]}</code>")
        elif data.startswith("coin_detail_"):
            symbol = data[len("coin_detail_"):]
            try:
                self._cmd_coin_detail(chat_id, symbol)
            except Exception as exc:
                logger.error("[TelegramBot] coin_detail %s failed: %s", symbol, exc)
                self._send(chat_id, f"⚠️ Error: <code>{str(exc)[:200]}</code>")
        else:
            self._send_menu(chat_id)

    # ── 📊 Open Trades ────────────────────────────────────────────────────────

    def _cmd_open_trades(self, chat_id: str) -> None:
        from app.data.repository import get_open_paper_trades
        from app.data.repository import get_candles

        symbols = self._watcher.symbols
        lines   = ["📊 <b>Open Trades</b>\n"]
        found   = False

        for sym in symbols:
            trades = get_open_paper_trades(sym)
            for t in trades:
                found = True
                direction  = t.get("direction", "?")
                entry      = float(t.get("entry_price", 0))
                sl         = float(t.get("stop_loss", 0))
                tp         = float(t.get("take_profit", 0))
                size       = float(t.get("position_size", 0))
                lev        = int(t.get("leverage") or 1)

                # Estimate unrealised PnL from latest candle
                df = get_candles(sym, "30m", limit=1)
                if not df.empty:
                    price   = float(df["close"].iloc[-1])
                    raw_pnl = (price - entry) * size if direction == "BUY" else (entry - price) * size
                    pnl_str = f"{'+'if raw_pnl >= 0 else ''}{raw_pnl:.2f}"
                    price_str = f"  Now: <code>{price:.4f}</code>  uPnL: <b>{pnl_str}</b>"
                else:
                    price_str = ""

                emoji = "🟢" if direction == "BUY" else "🔴"
                lev_s = f"  {lev}×" if lev > 1 else ""
                lines.append(
                    f"{emoji} <b>{sym}</b> {direction}{lev_s}\n"
                    f"  Entry: <code>{entry:.4f}</code>  SL: <code>{sl:.4f}</code>  TP: <code>{tp:.4f}</code>\n"
                    f"  Size: {size:.6f}{price_str}"
                )

        if not found:
            lines.append("No open trades at this time.")

        self._send(chat_id, "\n".join(lines))

    # ── 📉 Last Closed Trades ─────────────────────────────────────────────────

    def _cmd_last_closed(self, chat_id: str) -> None:
        from app.db.connection import get_conn
        from app.utils.timeframes import ms_to_dt

        sql = """
            SELECT symbol, direction, entry_price, close_price, pnl, pnl_pct,
                   status, close_time, leverage
            FROM paper_trades
            WHERE status != 'open'
            ORDER BY close_time DESC
            LIMIT 10
        """
        with get_conn() as conn:
            rows = [dict(r) for r in conn.execute(sql).fetchall()]

        lines = ["📉 <b>Last 10 Closed Trades</b>\n"]
        if not rows:
            lines.append("No closed trades yet.")
        for t in rows:
            pnl    = float(t.get("pnl") or 0)
            emoji  = "💰" if pnl >= 0 else "📉"
            pct    = float(t.get("pnl_pct") or 0)
            entry  = float(t.get("entry_price") or 0)
            close  = float(t.get("close_price") or 0)
            lev    = int(t.get("leverage") or 1)
            lev_s  = f" {lev}×" if lev > 1 else ""
            status = (t.get("status") or "closed").replace("_", " ")
            ct     = t.get("close_time")
            dt_s   = ms_to_dt(int(ct)).strftime("%m-%d %H:%M") if ct else "—"
            lines.append(
                f"{emoji} <b>{t['symbol']}</b> {t['direction']}{lev_s}  <i>{dt_s}</i>\n"
                f"  {entry:.4f} → {close:.4f}  "
                f"PnL: <b>{'+'if pnl>=0 else ''}{pnl:.2f}</b> ({pct:+.2f}%)  [{status}]"
            )

        self._send(chat_id, "\n".join(lines))

    # ── ⚠️ Risk Stats ─────────────────────────────────────────────────────────

    def _cmd_risk_stats(self, chat_id: str) -> None:
        from app.data.repository import get_all_open_paper_trades
        from app.paper.account import get_portfolio_capital

        max_total = 3  # _MAX_PORTFOLIO_TRADES
        max_dir   = 2  # _MAX_SAME_DIRECTION

        # DB-wide, matching the PositionManager guards (cross-process safe)
        all_open = get_all_open_paper_trades()

        buys  = [t for t in all_open if t.get("direction") == "BUY"]
        sells = [t for t in all_open if t.get("direction") == "SELL"]

        # Estimate total $ at risk (distance to SL × size)
        total_at_risk = 0.0
        for t in all_open:
            sym   = t["symbol"]
            entry = float(t.get("entry_price", 0))
            sl    = float(t.get("stop_loss", 0))
            size  = float(t.get("position_size", 0))
            direction = t.get("direction", "BUY")
            risk = abs(entry - sl) * size if entry and sl and size else 0
            total_at_risk += risk

        total_cap = get_portfolio_capital()

        lines = [
            "⚠️ <b>Risk Stats</b>\n",
            f"Open trades: <b>{len(all_open)}</b> / {max_total} cap",
            f"  🟢 BUYs:  {len(buys)} / {max_dir} cap",
            f"  🔴 SELLs: {len(sells)} / {max_dir} cap",
            f"\nPortfolio equity: <b>${total_cap:,.2f}</b>",
            f"Capital at risk: <b>${total_at_risk:,.2f}</b>",
        ]

        if total_cap > 0 and total_at_risk > 0:
            pct = total_at_risk / total_cap * 100
            lines.append(f"Risk %: <b>{pct:.2f}%</b> of portfolio")

        mode = "🛑 PAUSED" if self._watcher.dry_run else "✅ Active"
        lines.append(f"\nTrading mode: <b>{mode}</b>")

        self._send(chat_id, "\n".join(lines))

    # ── 📈 Latest Signals ─────────────────────────────────────────────────────

    def _cmd_latest_signals(self, chat_id: str) -> None:
        from app.data.repository import get_latest_signal
        from app.utils.timeframes import ms_to_dt

        symbols = self._watcher.symbols
        lines   = ["📈 <b>Latest Signals</b>\n"]

        for sym in symbols:
            sig = get_latest_signal(sym)
            if not sig:
                lines.append(f"<b>{sym}</b>: no signal yet")
                continue
            signal = sig.get("signal", "?")
            emoji  = {"BUY": "🟢", "SELL": "🔴", "HOLD": "🟡"}.get(signal, "⚪")
            conf   = sig.get("confidence", 0)
            regime = sig.get("market_regime", "—")
            zone_s = sig.get("zone_score", 0)
            ts     = sig.get("timestamp")
            dt_s   = ms_to_dt(int(ts)).strftime("%m-%d %H:%M") if ts else "—"
            lines.append(
                f"{emoji} <b>{sym}</b>: <b>{signal}</b>  conf={conf:.0f}  "
                f"regime={regime}  zone={zone_s:.0f}\n  <i>{dt_s}</i>"
            )

        self._send(chat_id, "\n".join(lines))

    # ── 🌍 Watchlist ──────────────────────────────────────────────────────────

    def _cmd_watchlist(self, chat_id: str) -> None:
        from app.data.repository import get_latest_signal

        symbols  = self._watcher.symbols
        cycle    = self._watcher._cycle
        rescan_n = self._watcher._rescan_every
        if rescan_n > 0:
            next_rescan = rescan_n - (cycle % rescan_n) if cycle % rescan_n != 0 else rescan_n
            rescan_str  = f"Next rescan in <b>{next_rescan}</b> cycles ({next_rescan * 30 // 60}h)"
        else:
            rescan_str = "Auto-rescan disabled"

        lines = [f"🌍 <b>Active Watchlist</b>  (cycle #{cycle})\n"]
        for i, sym in enumerate(symbols, 1):
            sig    = get_latest_signal(sym)
            regime = sig.get("market_regime", "—") if sig else "—"
            signal = sig.get("signal", "—") if sig else "—"
            emoji  = {"BUY": "🟢", "SELL": "🔴", "HOLD": "🟡"}.get(signal, "⚪")
            lines.append(f"{i}. {emoji} <b>{sym}</b>  regime={regime}  last={signal}")

        lines.append(f"\n{rescan_str}")
        self._send(chat_id, "\n".join(lines))

    # ── 🧠 ML Models ─────────────────────────────────────────────────────────

    def _cmd_ml_models(self, chat_id: str) -> None:
        from app.db.connection import get_conn
        import datetime

        cfg = get_settings()
        model_dir = cfg.model_dir

        lines = ["🧠 <b>ML Models</b>\n"]

        # Query active models from DB
        sql = """
            SELECT symbol, model_type, algorithm, version, created_at,
                   cv_accuracy, cv_f1
            FROM model_versions
            WHERE status = 'active'
            ORDER BY symbol, model_type
        """
        try:
            with get_conn() as conn:
                rows = [dict(r) for r in conn.execute(sql).fetchall()]
        except Exception:
            rows = []

        if rows:
            seen = {}
            for r in rows:
                sym  = r.get("symbol", "?")
                mtype = r.get("model_type", "?").upper()
                acc  = r.get("cv_accuracy")
                f1   = r.get("cv_f1")
                ver  = r.get("version", "?")
                acc_s = f"acc={acc:.2f}" if acc else ""
                f1_s  = f"f1={f1:.2f}" if f1 else ""
                metrics = "  ".join(filter(None, [acc_s, f1_s])) or "—"
                key = f"{sym}_{mtype}"
                seen[key] = f"<b>{sym}</b> {mtype}  {metrics}\n  <i>ver: {ver}</i>"
            lines += list(seen.values())
        else:
            # Fallback: scan joblib files
            if model_dir.exists():
                files = sorted(model_dir.glob("*.joblib"), key=lambda f: f.stat().st_mtime, reverse=True)
                if files:
                    lines.append(f"Found <b>{len(files)}</b> model files")
                    for f in files[:8]:
                        mtime = datetime.datetime.fromtimestamp(f.stat().st_mtime)
                        lines.append(f"  • {f.stem}  <i>{mtime.strftime('%Y-%m-%d %H:%M')}</i>")
                    if len(files) > 8:
                        lines.append(f"  … and {len(files) - 8} more")
                else:
                    lines.append("No model files found.")
            else:
                lines.append("Model directory not found.")

        self._send(chat_id, "\n".join(lines))

    # ── 🛑 Pause Trading ──────────────────────────────────────────────────────

    def _cmd_pause(self, chat_id: str) -> None:
        if self._watcher.dry_run:
            self._send(chat_id, "🛑 Trading is already <b>paused</b>.\nTap ▶️ Resume to re-enable.")
        else:
            self._watcher.dry_run = True
            logger.info("[TelegramBot] Trading paused via Telegram command")
            self._send(chat_id,
                "🛑 <b>Trading PAUSED</b>\n\n"
                "Signal generation continues but no new trades will be opened.\n"
                "Tap ▶️ Resume to re-enable."
            )

    # ── ▶️ Resume Trading ─────────────────────────────────────────────────────

    def _cmd_resume(self, chat_id: str) -> None:
        if not self._watcher.dry_run:
            self._send(chat_id, "✅ Trading is already <b>active</b>.")
        else:
            self._watcher.dry_run = False
            logger.info("[TelegramBot] Trading resumed via Telegram command")
            self._send(chat_id,
                "▶️ <b>Trading RESUMED</b>\n\n"
                "New trades will be opened on the next valid signal."
            )

    # ── 🔁 Force Rescan ───────────────────────────────────────────────────────

    def _cmd_force_rescan(self, chat_id: str) -> None:
        self._send(chat_id, "🔁 Rescan queued — will run at the start of the next cycle...")
        self._watcher._rescan_pending = True
        logger.info("[TelegramBot] Force rescan requested via Telegram")

    # ── 💰 Capital ────────────────────────────────────────────────────────────

    def _cmd_capital(self, chat_id: str) -> None:
        from app.paper.account import get_paper_capital, get_portfolio_capital

        symbols = self._watcher.symbols
        lines   = ["💰 <b>Paper Capital</b>\n"]
        lines.append(
            f"  <b>Portfolio equity:</b> <code>${get_portfolio_capital():,.2f}</code>"
            f"  (sizing basis)\n"
        )
        lines.append("  Per-symbol ledgers:")
        for sym in symbols:
            lines.append(f"    <b>{sym}</b>: <code>${get_paper_capital(sym):,.2f}</code>")
        self._send(chat_id, "\n".join(lines))

    # ── ❤️ Status ─────────────────────────────────────────────────────────────

    def _cmd_status(self, chat_id: str) -> None:
        from app.utils.timeframes import tf_to_ms, ms_to_dt
        import time as _time

        watcher   = self._watcher
        cycle     = watcher._cycle
        symbols   = watcher.symbols
        dry_run   = watcher.dry_run
        tf        = watcher.exec_tf
        ivl_ms    = watcher._interval_ms
        now_ms    = int(_time.time() * 1000)
        next_ms   = ((now_ms // ivl_ms) + 1) * ivl_ms
        wait_s    = (next_ms - now_ms) // 1000
        next_str  = ms_to_dt(next_ms).strftime("%H:%M:%S UTC")

        mode = "🛑 PAUSED (dry-run)" if dry_run else "✅ Active"
        lines = [
            "❤️ <b>Watcher Status</b>\n",
            f"Mode:         <b>{mode}</b>",
            f"Timeframe:    {tf}",
            f"Cycle #:      <b>{cycle}</b>",
            f"Symbols:      {', '.join(symbols)}",
            f"Next candle:  {next_str}  (in {wait_s // 60}m {wait_s % 60}s)",
        ]

        rescan_n = watcher._rescan_every
        if rescan_n > 0:
            nxt = rescan_n - (cycle % rescan_n) if cycle % rescan_n != 0 else rescan_n
            lines.append(f"Next rescan:  in {nxt} cycles ({nxt * 30 // 60}h)")

        self._send(chat_id, "\n".join(lines))

    # ── 📋 Summary ────────────────────────────────────────────────────────────

    def _cmd_summary(self, chat_id: str) -> None:
        from app.data.repository import get_paper_trade_summary

        stats = get_paper_trade_summary()
        total   = int(stats.get("total") or 0)
        wins    = int(stats.get("wins") or 0)
        losses  = int(stats.get("losses") or 0)
        pnl     = float(stats.get("total_pnl") or 0)
        best    = float(stats.get("best_trade") or 0)
        worst   = float(stats.get("worst_trade") or 0)
        win_pct = (wins / total * 100) if total > 0 else 0.0

        lines = [
            "📋 <b>All-Time Summary</b>\n",
            f"Total trades:  <b>{total}</b>",
            f"Wins / Losses: <b>{wins}</b> / <b>{losses}</b>  ({win_pct:.1f}% WR)",
            f"Total PnL:     <b>{'+'if pnl >= 0 else ''}{pnl:.2f}</b>",
            f"Best trade:    <code>+{best:.2f}</code>",
            f"Worst trade:   <code>{worst:.2f}</code>",
        ]

        # Per-symbol breakdown
        symbols = self._watcher.symbols
        sym_lines = []
        for sym in symbols:
            s = get_paper_trade_summary(sym)
            if not s or not s.get("total"):
                continue
            t  = int(s.get("total") or 0)
            w  = int(s.get("wins") or 0)
            p  = float(s.get("total_pnl") or 0)
            wr = (w / t * 100) if t > 0 else 0
            sym_lines.append(
                f"  <b>{sym}</b>: {t} trades  WR={wr:.0f}%  PnL={'+'if p>=0 else ''}{p:.2f}"
            )
        if sym_lines:
            lines.append("\n<b>By symbol:</b>")
            lines += sym_lines

        self._send(chat_id, "\n".join(lines))

    # ── 🪙 Coin Summary — symbol picker ──────────────────────────────────────

    def _cmd_coin_summary(self, chat_id: str) -> None:
        symbols = self._watcher.symbols
        # Build one button per watched symbol
        buttons = [[{"text": sym, "callback_data": f"coin_detail_{sym}"}] for sym in symbols]
        self._api("sendMessage", {
            "chat_id":      chat_id,
            "text":         "🪙 <b>Coin Summary</b>\n\nSelect a coin:",
            "parse_mode":   "HTML",
            "reply_markup": {"inline_keyboard": buttons},
        })

    # ── 🪙 Coin Detail — single coin ──────────────────────────────────────────

    def _cmd_coin_detail(self, chat_id: str, sym: str) -> None:
        from app.data.repository import get_candles, get_latest_signal, get_open_paper_trades
        from app.paper.account import get_paper_capital

        # 30m candles: 48 = 24h price stats, 336 = 7d high/low
        df     = get_candles(sym, "30m", limit=337)
        if df.empty:
            self._send(chat_id, f"⚠️ No candle data for <b>{sym}</b>.")
            return

        df_24h = df.tail(48)
        df_7d  = df

        price    = float(df["close"].iloc[-1])
        open_24h = float(df_24h["open"].iloc[0])
        high_24h = float(df_24h["high"].max())
        low_24h  = float(df_24h["low"].min())
        high_7d  = float(df_7d["high"].max())
        low_7d   = float(df_7d["low"].min())
        qvol_24h = float(df_24h["quote_volume"].sum()) if "quote_volume" in df_24h.columns else 0.0
        chg_pct  = (price - open_24h) / open_24h * 100 if open_24h else 0.0
        chg_abs  = price - open_24h

        chg_emoji = "🟢" if chg_pct >= 0 else "🔴"

        if qvol_24h >= 1_000_000_000:
            vol_str = f"${qvol_24h/1_000_000_000:.2f}B"
        elif qvol_24h >= 1_000_000:
            vol_str = f"${qvol_24h/1_000_000:.1f}M"
        elif qvol_24h >= 1_000:
            vol_str = f"${qvol_24h/1_000:.1f}K"
        else:
            vol_str = f"${qvol_24h:.0f}"

        # 1h candle for last-candle detail
        df_1h = get_candles(sym, "1h", limit=2)
        prev_close = float(df_1h["close"].iloc[-2]) if len(df_1h) >= 2 else None

        # Strategy overlay
        sig       = get_latest_signal(sym)
        regime    = sig.get("market_regime", "—") if sig else "—"
        signal    = sig.get("signal", "—") if sig else "—"
        conf      = sig.get("confidence", 0) if sig else 0
        long_s    = sig.get("long_score", 0) if sig else 0
        short_s   = sig.get("short_score", 0) if sig else 0
        zone_s    = sig.get("zone_score", 0) if sig else 0
        zone_r    = sig.get("zone_rating", "—") if sig else "—"
        h4_bias   = sig.get("h4_bias", "—") if sig else "—"
        h1_conf   = sig.get("h1_confirmation", "—") if sig else "—"
        setup     = sig.get("setup_type", "—") if sig else "—"
        sweep     = sig.get("liquidity_sweep", False) if sig else False
        sig_emoji = {"BUY": "🟢", "SELL": "🔴", "HOLD": "🟡"}.get(signal, "⚪")

        if signal in ("BUY", "SELL") and sig:
            entry_p = sig.get("entry_price", 0)
            sl_p    = sig.get("stop_loss", 0)
            tp_p    = sig.get("take_profit", 0)
            rr      = sig.get("risk_reward", 0)
            trade_setup = (
                f"  Entry: <code>{entry_p:.4f}</code>  SL: <code>{sl_p:.4f}</code>  TP: <code>{tp_p:.4f}</code>\n"
                f"  R:R: 1:{rr:.2f}  Setup: {setup}"
            )
        else:
            trade_setup = f"  Setup: {setup}"

        # Open paper trade
        trades = get_open_paper_trades(sym)
        if trades:
            t         = trades[0]
            direction = t.get("direction", "?")
            entry     = float(t.get("entry_price", 0))
            sl        = float(t.get("stop_loss", 0))
            tp        = float(t.get("take_profit", 0))
            size      = float(t.get("position_size", 0))
            lev       = int(t.get("leverage") or 1)
            raw_pnl   = (price - entry) * size if direction == "BUY" else (entry - price) * size
            lev_s     = f" {lev}×" if lev > 1 else ""
            dir_emoji = "🟢" if direction == "BUY" else "🔴"
            open_trade_str = (
                f"\n📌 <b>Open Trade</b>\n"
                f"  {dir_emoji} {direction}{lev_s} @ <code>{entry:.4f}</code>\n"
                f"  SL: <code>{sl:.4f}</code>  TP: <code>{tp:.4f}</code>\n"
                f"  Size: {size:.6f}  uPnL: <b>{'+'if raw_pnl>=0 else ''}{raw_pnl:.2f}</b>"
            )
        else:
            open_trade_str = "\n📌 <b>No open trade</b>"

        capital = get_paper_capital(sym)

        # ── Performance stats ─────────────────────────────────────────────────
        from app.db.connection import get_conn
        from app.data.repository import get_paper_trade_summary

        perf = get_paper_trade_summary(sym)
        total  = int(perf.get("total") or 0)
        wins   = int(perf.get("wins") or 0)
        losses = int(perf.get("losses") or 0)
        t_pnl  = float(perf.get("total_pnl") or 0)
        best   = float(perf.get("best_trade") or 0)
        worst  = float(perf.get("worst_trade") or 0)
        wr_pct = (wins / total * 100) if total > 0 else 0.0

        # Avg win, avg loss, profit factor, avg duration
        ext_sql = """
            SELECT
                AVG(CASE WHEN pnl > 0 THEN pnl END)        AS avg_win,
                AVG(CASE WHEN pnl < 0 THEN pnl END)        AS avg_loss,
                SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END) AS gross_win,
                SUM(CASE WHEN pnl < 0 THEN pnl ELSE 0 END) AS gross_loss,
                AVG((close_time - open_time) / 60000.0)    AS avg_dur_min
            FROM paper_trades
            WHERE symbol = ? AND status != 'open'
        """
        with get_conn() as conn:
            ext = dict(conn.execute(ext_sql, (sym,)).fetchone() or {})

        avg_win   = float(ext.get("avg_win") or 0)
        avg_loss  = float(ext.get("avg_loss") or 0)
        gross_win = float(ext.get("gross_win") or 0)
        gross_loss= float(ext.get("gross_loss") or 0)
        avg_dur   = float(ext.get("avg_dur_min") or 0)

        pf = (gross_win / abs(gross_loss)) if gross_loss and gross_loss != 0 else 0.0

        dur_str = f"{int(avg_dur // 60)}h {int(avg_dur % 60)}m" if avg_dur else "—"

        # Current streak: walk last 20 closed trades newest-first
        streak_sql = """
            SELECT pnl FROM paper_trades
            WHERE symbol = ? AND status != 'open'
            ORDER BY close_time DESC LIMIT 20
        """
        with get_conn() as conn:
            streak_rows = [r[0] for r in conn.execute(streak_sql, (sym,)).fetchall()]

        streak_count = 0
        streak_type  = None
        for pnl_val in streak_rows:
            result = "W" if (pnl_val or 0) >= 0 else "L"
            if streak_type is None:
                streak_type = result
            if result == streak_type:
                streak_count += 1
            else:
                break

        if streak_count >= 2 and streak_type:
            streak_emoji = "🔥" if streak_type == "W" else "❄️"
            streak_label = "win" if streak_type == "W" else "loss"
            streak_str   = f"{streak_emoji} {streak_count} {streak_label}s in a row"
        else:
            streak_str = "—"

        if total > 0:
            perf_lines = [
                f"",
                f"📊 <b>Performance</b>  ({total} trades)",
                f"  Win Rate:  <b>{wr_pct:.1f}%</b>  ({wins}W / {losses}L)",
                f"  Total PnL: <b>{'+'if t_pnl>=0 else ''}{t_pnl:.2f}</b>",
                f"  Avg Win:   +{avg_win:.2f}   Avg Loss: {avg_loss:.2f}",
                f"  Prof Factor: <b>{pf:.2f}</b>",
                f"  Best: +{best:.2f}" + (f"   Worst: {worst:.2f}" if worst < 0 else ""),
                f"  Streak:   {streak_str}",
                f"  Avg Dur:  {dur_str}",
            ]
        else:
            perf_lines = [f"", f"📊 <b>Performance</b>", f"  No closed trades yet."]

        lines = [
            f"🪙 <b>{sym}</b>",
            f"",
            f"<b>Price</b>",
            f"  Now:     <code>{price:.4f}</code>",
            f"  24h Chg: {chg_emoji} {'+'if chg_pct>=0 else ''}{chg_pct:.2f}%  ({'+'if chg_abs>=0 else ''}{chg_abs:.4f})",
            f"  24h H/L: <code>{high_24h:.4f}</code> / <code>{low_24h:.4f}</code>",
            f"  7d  H/L: <code>{high_7d:.4f}</code> / <code>{low_7d:.4f}</code>",
            f"  24h Vol: {vol_str}",
            f"",
            f"<b>Strategy</b>",
            f"  Regime:  {regime}",
            f"  4H Bias: {h4_bias}  1H: {h1_conf}",
            f"  Zone:    {zone_r} ({zone_s:.0f})  Sweep: {'✅' if sweep else '—'}",
            f"  Signal:  {sig_emoji} <b>{signal}</b>  conf={conf:.0f}",
            f"  MTF:     L={long_s:.1f}  S={short_s:.1f}",
            trade_setup,
            open_trade_str,
            *perf_lines,
            f"",
            f"💰 Capital: <code>${capital:,.2f}</code>",
        ]

        self._send(chat_id, "\n".join(lines))
