"""
Live trading Telegram bot listener.

Long-polling getUpdates loop in a background daemon thread. Responds only to
LIVE_TELEGRAM_CHAT_ID. Reads from live_trades (not paper_trades). Provides
all paper-bot features plus live-specific commands: 💰 Live Balance,
🚨 Close All (2-step confirmation), 🎯 Close Symbol (symbol picker).

Activate: LiveTelegramCommandListener(watcher, executor, client).start()
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

import httpx

from app.config import get_settings
from app.utils.logger import get_logger

if TYPE_CHECKING:
    from app.live.executor import LiveExecutor
    from app.live.exchange import BinanceExchangeClient
    from app.live.live_watcher import LiveTradingWatcher

logger = get_logger(__name__)

_BASE = "https://api.telegram.org"

_MENU_KEYBOARD = {
    "inline_keyboard": [
        [
            {"text": "📊 Open Positions", "callback_data": "lv_open"},
            {"text": "📉 Last Closed",    "callback_data": "lv_last_closed"},
            {"text": "💰 Live Balance",   "callback_data": "lv_balance"},
        ],
        [
            {"text": "📈 Latest Signals", "callback_data": "lv_signals"},
            {"text": "🌍 Watchlist",      "callback_data": "lv_watchlist"},
            {"text": "🧠 ML Models",      "callback_data": "lv_ml_models"},
        ],
        [
            {"text": "🛑 Pause",          "callback_data": "lv_pause"},
            {"text": "▶️ Resume",          "callback_data": "lv_resume"},
            {"text": "🔁 Force Rescan",   "callback_data": "lv_rescan"},
        ],
        [
            {"text": "⚠️ Risk Stats",     "callback_data": "lv_risk_stats"},
            {"text": "❤️ Status",         "callback_data": "lv_status"},
            {"text": "📋 Summary",        "callback_data": "lv_summary"},
        ],
        [
            {"text": "🚨 Close All",      "callback_data": "lv_close_all"},
            {"text": "🎯 Close Symbol",   "callback_data": "lv_close_sym"},
            {"text": "🪙 Coin Detail",    "callback_data": "lv_coin_detail"},
        ],
    ]
}


class LiveTelegramCommandListener:
    """
    Background bot for the live trading channel.

    Parameters
    ----------
    watcher  : LiveTradingWatcher
    executor : LiveExecutor
    client   : BinanceExchangeClient
    """

    def __init__(
        self,
        watcher: LiveTradingWatcher,
        executor: LiveExecutor,
        client: BinanceExchangeClient,
    ) -> None:
        self._watcher  = watcher
        self._executor = executor
        self._client   = client
        self._thread: threading.Thread | None = None
        self._stop_flag = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._stop_flag = False
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="live-telegram-bot",
            daemon=True,
        )
        self._thread.start()
        logger.info("[LiveBot] Listener started")

    def stop(self) -> None:
        self._stop_flag = True

    # ── Long-polling loop ─────────────────────────────────────────────────────

    def _poll_loop(self) -> None:
        cfg   = get_settings()
        token = getattr(cfg, "live_telegram_token", "")
        if not token:
            logger.debug("[LiveBot] No token configured — inactive")
            return

        offset = 0
        while not self._stop_flag:
            try:
                updates = self._get_updates(token, offset, timeout=20)
                for upd in updates:
                    offset = upd["update_id"] + 1
                    self._dispatch(upd)
            except Exception as exc:
                logger.debug("[LiveBot] Poll error: %s", exc)
                time.sleep(5)

    def _get_updates(self, token: str, offset: int, timeout: int = 20) -> list[dict]:
        url = f"{_BASE}/bot{token}/getUpdates"
        try:
            resp = httpx.get(
                url,
                params={
                    "offset":           offset,
                    "timeout":          timeout,
                    "allowed_updates":  ["message", "callback_query"],
                },
                timeout=timeout + 5,
            )
            data = resp.json()
            return data.get("result", []) if data.get("ok") else []
        except Exception:
            return []

    # ── Dispatch ──────────────────────────────────────────────────────────────

    def _dispatch(self, update: dict) -> None:
        cfg     = get_settings()
        chat_id = str(getattr(cfg, "live_telegram_chat_id", ""))

        if "message" in update:
            msg     = update["message"]
            from_id = str(msg.get("chat", {}).get("id", ""))
            if from_id != chat_id:
                return
            text = msg.get("text", "").strip()
            if text.startswith("/menu") or text.lower() in ("menu", "help"):
                self._send_menu(chat_id)
            elif text.startswith("/"):
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

    # ── Low-level helpers ─────────────────────────────────────────────────────

    def _token(self) -> str:
        return getattr(get_settings(), "live_telegram_token", "")

    def _api(self, method: str, payload: dict) -> bool:
        token = self._token()
        if not token:
            return False
        try:
            resp = httpx.post(
                f"{_BASE}/bot{token}/{method}", json=payload, timeout=10
            )
            return resp.status_code == 200
        except Exception as exc:
            logger.debug("[LiveBot] API %s failed: %s", method, exc)
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
            "text":         "🔴 <b>LQ-MTF Live Control Panel</b>\n\nSelect an option:",
            "parse_mode":   "HTML",
            "reply_markup": _MENU_KEYBOARD,
        })

    def _answer_callback(self, cq_id: str) -> None:
        self._api("answerCallbackQuery", {"callback_query_id": cq_id})

    def _inline(self, chat_id: str, text: str, buttons: list[list[dict]]) -> bool:
        return self._api("sendMessage", {
            "chat_id":      chat_id,
            "text":         text,
            "parse_mode":   "HTML",
            "reply_markup": {"inline_keyboard": buttons},
        })

    # ── Callback dispatcher ───────────────────────────────────────────────────

    def _handle_callback(self, chat_id: str, data: str) -> None:
        handlers = {
            "lv_open":        self._cmd_open_positions,
            "lv_last_closed": self._cmd_last_closed,
            "lv_balance":     self._cmd_live_balance,
            "lv_signals":     self._cmd_latest_signals,
            "lv_watchlist":   self._cmd_watchlist,
            "lv_ml_models":   self._cmd_ml_models,
            "lv_pause":       self._cmd_pause,
            "lv_resume":      self._cmd_resume,
            "lv_rescan":      self._cmd_force_rescan,
            "lv_risk_stats":  self._cmd_risk_stats,
            "lv_status":      self._cmd_status,
            "lv_summary":     self._cmd_summary,
            "lv_close_all":   self._cmd_close_all,
            "lv_close_all_yes": self._cmd_close_all_confirm,
            "lv_close_all_no":  lambda cid: self._send(cid, "❌ Cancelled."),
            "lv_close_sym":   self._cmd_close_symbol,
            "lv_close_sym_no": lambda cid: self._send(cid, "❌ Cancelled."),
            "lv_coin_detail": self._cmd_coin_detail_picker,
        }

        fn = handlers.get(data)
        if fn:
            try:
                fn(chat_id)
            except Exception as exc:
                logger.error("[LiveBot] Handler %s failed: %s", data, exc)
                self._send(chat_id, f"⚠️ Error: <code>{str(exc)[:200]}</code>")
            return

        # Parameterised callbacks
        if data.startswith("lv_close_sym_yes_"):
            symbol = data[len("lv_close_sym_yes_"):]
            self._cmd_close_symbol_confirm(chat_id, symbol)
        elif data.startswith("lv_close_sym_"):
            symbol = data[len("lv_close_sym_"):]
            self._cmd_close_symbol_ask(chat_id, symbol)
        elif data.startswith("lv_coin_detail_"):
            symbol = data[len("lv_coin_detail_"):]
            self._cmd_coin_detail(chat_id, symbol)
        else:
            self._send_menu(chat_id)

    # ── 📊 Open Positions ─────────────────────────────────────────────────────

    def _cmd_open_positions(self, chat_id: str) -> None:
        from app.data.repository import get_candles

        trades = self._executor.all_open_trades()
        lines  = ["📊 <b>Open Live Positions</b>\n"]
        if not trades:
            lines.append("No open live positions.")
        for t in trades:
            sym   = t.get("symbol", "?")
            dir_  = t.get("direction", "?")
            entry = float(t.get("entry_price") or 0)
            sl    = float(t.get("stop_loss") or 0)
            tp    = float(t.get("take_profit") or 0)
            size  = float(t.get("position_size") or 0)
            lev   = int(t.get("leverage") or 1)
            ptaken= int(t.get("partial_taken") or 0)
            lev_s = f"  {lev}×" if lev > 1 else ""
            emoji = "🟢" if dir_ == "BUY" else "🔴"

            df = get_candles(sym, "30m", limit=1)
            if not df.empty:
                price   = float(df["close"].iloc[-1])
                rem     = float(t.get("remaining_size") or size)
                raw_pnl = (price - entry) * rem if dir_ == "BUY" else (entry - price) * rem
                price_s = f"  Now: <code>{price:.4f}</code>  uPnL: <b>{'+'if raw_pnl>=0 else ''}{raw_pnl:.2f}</b>"
            else:
                price_s = ""

            partial_s = "  [Partial TP taken]" if ptaken else ""
            lines.append(
                f"{emoji} <b>{sym}</b> {dir_}{lev_s}{partial_s}\n"
                f"  Entry: <code>{entry:.4f}</code>  SL: <code>{sl:.4f}</code>  TP: <code>{tp:.4f}</code>\n"
                f"  Size: {size:.6f}{price_s}"
            )
        self._send(chat_id, "\n".join(lines))

    # ── 📉 Last Closed ────────────────────────────────────────────────────────

    def _cmd_last_closed(self, chat_id: str) -> None:
        from app.db.connection import get_conn
        from app.utils.timeframes import ms_to_dt

        with get_conn() as conn:
            rows = [dict(r) for r in conn.execute(
                """
                SELECT symbol, direction, entry_price, close_price, pnl, pnl_pct,
                       status, close_time, leverage
                FROM live_trades
                WHERE status != 'open'
                ORDER BY close_time DESC LIMIT 10
                """
            ).fetchall()]

        lines = ["📉 <b>Last 10 Closed Live Trades</b>\n"]
        if not rows:
            lines.append("No closed live trades yet.")
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

    # ── 💰 Live Balance ───────────────────────────────────────────────────────

    def _cmd_live_balance(self, chat_id: str) -> None:
        try:
            bal = self._client.get_wallet_balance()
        except Exception as exc:
            self._send(chat_id, f"⚠️ Could not fetch balance: <code>{exc}</code>")
            return
        cfg = get_settings()
        net_label = "🟡 TESTNET" if cfg.binance_testnet else "🔴 LIVE"
        text = (
            f"💰 <b>Live Balance ({net_label})</b>\n\n"
            f"Wallet:       <code>${bal['wallet']:,.2f}</code>\n"
            f"Available:    <code>${bal['available']:,.2f}</code>\n"
            f"Unrealised P&L: <code>{'+'if bal['unrealized']>=0 else ''}{bal['unrealized']:,.2f}</code>\n"
            f"Margin used:  <code>${bal['margin_used']:,.2f}</code>"
        )
        self._send(chat_id, text)

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
            ts     = sig.get("timestamp")
            dt_s   = ms_to_dt(int(ts)).strftime("%m-%d %H:%M") if ts else "—"
            lines.append(
                f"{emoji} <b>{sym}</b>: <b>{signal}</b>  conf={conf:.0f}  "
                f"regime={regime}\n  <i>{dt_s}</i>"
            )
        self._send(chat_id, "\n".join(lines))

    # ── 🌍 Watchlist ──────────────────────────────────────────────────────────

    def _cmd_watchlist(self, chat_id: str) -> None:
        from app.data.repository import get_latest_signal

        symbols  = self._watcher.symbols
        cycle    = self._watcher._cycle
        rescan_n = getattr(self._watcher, "_rescan_every", 0)
        if rescan_n > 0:
            nxt = rescan_n - (cycle % rescan_n) if cycle % rescan_n != 0 else rescan_n
            rescan_str = f"Next rescan in <b>{nxt}</b> cycles"
        else:
            rescan_str = "Auto-rescan disabled"

        lines = [f"🌍 <b>Active Watchlist</b>  (cycle #{cycle})\n"]
        for i, sym in enumerate(symbols, 1):
            sig    = get_latest_signal(sym)
            signal = sig.get("signal", "—") if sig else "—"
            regime = sig.get("market_regime", "—") if sig else "—"
            emoji  = {"BUY": "🟢", "SELL": "🔴", "HOLD": "🟡"}.get(signal, "⚪")
            lines.append(f"{i}. {emoji} <b>{sym}</b>  regime={regime}  last={signal}")
        lines.append(f"\n{rescan_str}")
        self._send(chat_id, "\n".join(lines))

    # ── 🧠 ML Models ─────────────────────────────────────────────────────────

    def _cmd_ml_models(self, chat_id: str) -> None:
        from app.db.connection import get_conn
        import datetime

        cfg = get_settings()
        lines = ["🧠 <b>ML Models</b>\n"]
        try:
            with get_conn() as conn:
                rows = [dict(r) for r in conn.execute(
                    "SELECT symbol, model_type, algorithm, version, cv_accuracy, cv_f1 "
                    "FROM model_versions WHERE status='active' ORDER BY symbol, model_type"
                ).fetchall()]
        except Exception:
            rows = []

        if rows:
            for r in rows:
                acc_s = f"acc={r['cv_accuracy']:.2f}" if r.get("cv_accuracy") else ""
                f1_s  = f"f1={r['cv_f1']:.2f}" if r.get("cv_f1") else ""
                metrics = "  ".join(filter(None, [acc_s, f1_s])) or "—"
                lines.append(
                    f"<b>{r['symbol']}</b> {r['model_type'].upper()}  {metrics}\n"
                    f"  <i>ver: {r['version']}</i>"
                )
        else:
            model_dir = cfg.model_dir
            if model_dir.exists():
                files = sorted(model_dir.glob("*.joblib"), key=lambda f: f.stat().st_mtime, reverse=True)
                lines.append(f"Found <b>{len(files)}</b> model files" if files else "No models.")
            else:
                lines.append("Model dir not found.")
        self._send(chat_id, "\n".join(lines))

    # ── 🛑 Pause / ▶️ Resume ──────────────────────────────────────────────────

    def _cmd_pause(self, chat_id: str) -> None:
        if getattr(self._watcher, "dry_run", False):
            self._send(chat_id, "🛑 Already <b>paused</b>. Tap ▶️ Resume to re-enable.")
        else:
            self._watcher.dry_run = True
            logger.info("[LiveBot] Trading paused via Telegram")
            self._send(chat_id,
                "🛑 <b>Trading PAUSED</b>\n\n"
                "No new live entries. Existing bracket orders still active on exchange."
            )

    def _cmd_resume(self, chat_id: str) -> None:
        if not getattr(self._watcher, "dry_run", False):
            self._send(chat_id, "✅ Already <b>active</b>.")
        else:
            self._watcher.dry_run = False
            logger.info("[LiveBot] Trading resumed via Telegram")
            self._send(chat_id, "▶️ <b>Trading RESUMED</b>\n\nNew signals will be traded.")

    # ── 🔁 Force Rescan ───────────────────────────────────────────────────────

    def _cmd_force_rescan(self, chat_id: str) -> None:
        self._watcher._rescan_pending = True
        self._send(chat_id, "🔁 Rescan queued — will run at next cycle start.")

    # ── ⚠️ Risk Stats ─────────────────────────────────────────────────────────

    def _cmd_risk_stats(self, chat_id: str) -> None:
        cfg    = get_settings()
        trades = self._executor.all_open_trades()
        buys   = [t for t in trades if t.get("direction") == "BUY"]
        sells  = [t for t in trades if t.get("direction") == "SELL"]

        total_at_risk = sum(
            abs(float(t.get("entry_price", 0)) - float(t.get("stop_loss", 0)))
            * float(t.get("remaining_size") or t.get("position_size", 0))
            for t in trades
        )

        try:
            balance = self._client.get_balance()
        except Exception:
            balance = 0.0

        daily_loss_limit = -(balance * cfg.live_max_daily_loss_pct / 100)

        from app.db.connection import get_conn
        import time as _time
        today_start = int(_time.time() * 1000) - (int(_time.time() * 1000) % 86_400_000)
        with get_conn() as conn:
            daily_pnl = conn.execute(
                "SELECT SUM(CAST(pnl AS REAL)) FROM live_trades "
                "WHERE status!='open' AND close_time>=?",
                (today_start,),
            ).fetchone()[0] or 0.0

        mode = "🛑 PAUSED" if getattr(self._watcher, "dry_run", False) else "✅ Active"
        lines = [
            "⚠️ <b>Live Risk Stats</b>\n",
            f"Open positions: <b>{len(trades)}</b> / {cfg.live_max_portfolio} cap",
            f"  🟢 BUYs:  {len(buys)} / {cfg.live_max_same_dir} cap",
            f"  🔴 SELLs: {len(sells)} / {cfg.live_max_same_dir} cap",
            f"\nBalance (free): <b>${balance:,.2f} USDT</b>",
            f"Capital at risk: <b>${total_at_risk:,.2f}</b>",
            f"Today PnL: <b>{'+'if daily_pnl>=0 else ''}{daily_pnl:.2f}</b>  "
            f"(limit: {daily_loss_limit:.2f})",
            f"\nMode: <b>{mode}</b>",
        ]
        self._send(chat_id, "\n".join(lines))

    # ── ❤️ Status ─────────────────────────────────────────────────────────────

    def _cmd_status(self, chat_id: str) -> None:
        import time as _time
        from app.utils.timeframes import ms_to_dt

        cfg      = get_settings()
        watcher  = self._watcher
        cycle    = watcher._cycle
        tf       = getattr(watcher, "exec_tf", "30m")
        ivl_ms   = getattr(watcher, "_interval_ms", 30 * 60 * 1000)
        now_ms   = int(_time.time() * 1000)
        next_ms  = ((now_ms // ivl_ms) + 1) * ivl_ms
        wait_s   = (next_ms - now_ms) // 1000
        net_label = "🟡 TESTNET" if cfg.binance_testnet else "🔴 MAINNET"
        mode = "🛑 PAUSED" if getattr(watcher, "dry_run", False) else "✅ Active"
        lines = [
            "❤️ <b>Live Watcher Status</b>\n",
            f"Mode:         <b>{mode}</b>  [{net_label}]",
            f"Timeframe:    {tf}",
            f"Cycle #:      <b>{cycle}</b>",
            f"Symbols:      {', '.join(watcher.symbols)}",
            f"Next candle:  in {wait_s // 60}m {wait_s % 60}s",
            f"Max leverage: <b>{cfg.live_max_leverage}×</b>",
        ]
        self._send(chat_id, "\n".join(lines))

    # ── 📋 Summary ────────────────────────────────────────────────────────────

    def _cmd_summary(self, chat_id: str) -> None:
        from app.db.connection import get_conn

        with get_conn() as conn:
            row = dict(conn.execute(
                """
                SELECT
                    COUNT(*)                                          AS total,
                    SUM(CASE WHEN CAST(pnl AS REAL)>0 THEN 1 ELSE 0 END) AS wins,
                    SUM(CASE WHEN CAST(pnl AS REAL)<0 THEN 1 ELSE 0 END) AS losses,
                    SUM(CAST(pnl AS REAL))                            AS total_pnl,
                    MAX(CAST(pnl AS REAL))                            AS best,
                    MIN(CAST(pnl AS REAL))                            AS worst,
                    SUM(CASE WHEN CAST(pnl AS REAL)>0 THEN CAST(pnl AS REAL) ELSE 0 END) AS gross_win,
                    SUM(CASE WHEN CAST(pnl AS REAL)<0 THEN CAST(pnl AS REAL) ELSE 0 END) AS gross_loss
                FROM live_trades WHERE status != 'open'
                """
            ).fetchone() or {})

        total     = int(row.get("total") or 0)
        wins      = int(row.get("wins") or 0)
        losses    = int(row.get("losses") or 0)
        total_pnl = float(row.get("total_pnl") or 0)
        best      = float(row.get("best") or 0)
        worst     = float(row.get("worst") or 0)
        gross_win = float(row.get("gross_win") or 0)
        gross_loss= float(row.get("gross_loss") or 0)
        wr_pct    = (wins / total * 100) if total else 0
        pf        = (gross_win / abs(gross_loss)) if gross_loss else 0

        lines = [
            "📋 <b>Live All-Time Summary</b>\n",
            f"Total trades:  <b>{total}</b>",
            f"Wins / Losses: <b>{wins}</b> / <b>{losses}</b>  ({wr_pct:.1f}% WR)",
            f"Total PnL:     <b>{'+'if total_pnl>=0 else ''}{total_pnl:.2f}</b>",
            f"Profit Factor: <b>{pf:.2f}</b>",
            f"Best:  <code>+{best:.2f}</code>   Worst: <code>{worst:.2f}</code>",
        ]
        self._send(chat_id, "\n".join(lines))

    # ── 🚨 Close All ─────────────────────────────────────────────────────────

    def _cmd_close_all(self, chat_id: str) -> None:
        from app.data.repository import get_candles

        trades = self._executor.all_open_trades()
        if not trades:
            self._send(chat_id, "No open live positions.")
            return

        lines = [f"🚨 <b>Close All Positions ({len(trades)})</b>\n"]
        for t in trades:
            sym  = t.get("symbol", "?")
            dir_ = t.get("direction", "?")
            entry= float(t.get("entry_price") or 0)
            lev  = int(t.get("leverage") or 1)
            lev_s= f" {lev}×" if lev > 1 else ""
            df   = get_candles(sym, "30m", limit=1)
            if not df.empty:
                price   = float(df["close"].iloc[-1])
                rem     = float(t.get("remaining_size") or t.get("position_size", 0))
                pnl     = (price-entry)*rem if dir_=="BUY" else (entry-price)*rem
                pnl_s   = f"  uPnL: {'+'if pnl>=0 else ''}{pnl:.2f}"
            else:
                pnl_s = ""
            lines.append(f"  {sym} {dir_}{lev_s} @ {entry:.4f}{pnl_s}")

        lines.append("\n⚠️ <b>This will market-close all positions immediately.</b>")
        self._inline(chat_id, "\n".join(lines), [
            [
                {"text": f"✅ Yes, close all {len(trades)} positions",
                 "callback_data": "lv_close_all_yes"},
                {"text": "❌ Cancel",
                 "callback_data": "lv_close_all_no"},
            ]
        ])

    def _cmd_close_all_confirm(self, chat_id: str) -> None:
        trades = self._executor.all_open_trades()
        if not trades:
            self._send(chat_id, "No open positions to close.")
            return

        total_pnl = 0.0
        closed = 0
        for t in trades:
            try:
                pnl = self._executor.emergency_close(t["symbol"])
                total_pnl += pnl
                closed += 1
            except Exception as exc:
                logger.error("[LiveBot] emergency_close %s failed: %s", t["symbol"], exc)

        sign = "+" if total_pnl >= 0 else ""
        self._send(
            chat_id,
            f"🔴 [LIVE] Closed <b>{closed}</b> position(s).\n"
            f"Total realised PnL: <b>{sign}{total_pnl:.2f}</b> USDT"
        )

    # ── 🎯 Close Symbol ───────────────────────────────────────────────────────

    def _cmd_close_symbol(self, chat_id: str) -> None:
        from app.data.repository import get_candles

        trades = self._executor.all_open_trades()
        if not trades:
            self._send(chat_id, "No open live positions.")
            return

        buttons = []
        for t in trades:
            sym   = t.get("symbol", "?")
            dir_  = t.get("direction", "?")
            entry = float(t.get("entry_price") or 0)
            df    = get_candles(sym, "30m", limit=1)
            if not df.empty:
                price = float(df["close"].iloc[-1])
                rem   = float(t.get("remaining_size") or t.get("position_size", 0))
                pnl   = (price-entry)*rem if dir_=="BUY" else (entry-price)*rem
                label = f"{sym} {dir_}  uPnL={'+'if pnl>=0 else ''}{pnl:.2f}"
            else:
                label = f"{sym} {dir_}"
            buttons.append([{"text": label, "callback_data": f"lv_close_sym_{sym}"}])

        self._inline(chat_id, "🎯 <b>Select position to close:</b>", buttons)

    def _cmd_close_symbol_ask(self, chat_id: str, symbol: str) -> None:
        from app.data.repository import get_candles

        trades = [t for t in self._executor.all_open_trades() if t.get("symbol") == symbol]
        if not trades:
            self._send(chat_id, f"No open live position for {symbol}.")
            return

        t     = trades[0]
        dir_  = t.get("direction", "?")
        entry = float(t.get("entry_price") or 0)
        df    = get_candles(symbol, "30m", limit=1)
        if not df.empty:
            price = float(df["close"].iloc[-1])
            rem   = float(t.get("remaining_size") or t.get("position_size", 0))
            pnl   = (price-entry)*rem if dir_=="BUY" else (entry-price)*rem
            pnl_s = f"\nuPnL: {'+'if pnl>=0 else ''}{pnl:.2f}"
        else:
            pnl_s = ""

        self._inline(
            chat_id,
            f"🎯 Close <b>{symbol}</b> {dir_} @ <code>{entry:.4f}</code>?{pnl_s}",
            [
                [
                    {"text": "✅ Confirm", "callback_data": f"lv_close_sym_yes_{symbol}"},
                    {"text": "❌ Cancel",  "callback_data": "lv_close_sym_no"},
                ]
            ],
        )

    def _cmd_close_symbol_confirm(self, chat_id: str, symbol: str) -> None:
        try:
            pnl = self._executor.emergency_close(symbol)
            sign = "+" if pnl >= 0 else ""
            self._send(
                chat_id,
                f"🔴 [LIVE] <b>{symbol}</b> closed.\nRealised PnL: <b>{sign}{pnl:.2f}</b> USDT"
            )
        except Exception as exc:
            self._send(chat_id, f"⚠️ Close failed: <code>{str(exc)[:200]}</code>")

    # ── 🪙 Coin Detail ────────────────────────────────────────────────────────

    def _cmd_coin_detail_picker(self, chat_id: str) -> None:
        symbols = self._watcher.symbols
        buttons = [[{"text": sym, "callback_data": f"lv_coin_detail_{sym}"}] for sym in symbols]
        self._inline(chat_id, "🪙 <b>Select a coin:</b>", buttons)

    def _cmd_coin_detail(self, chat_id: str, sym: str) -> None:
        from app.data.repository import get_candles, get_latest_signal
        from app.db.connection import get_conn

        df = get_candles(sym, "30m", limit=337)
        if df.empty:
            self._send(chat_id, f"⚠️ No candle data for <b>{sym}</b>.")
            return

        df_24h   = df.tail(48)
        price    = float(df["close"].iloc[-1])
        open_24h = float(df_24h["open"].iloc[0])
        high_24h = float(df_24h["high"].max())
        low_24h  = float(df_24h["low"].min())
        high_7d  = float(df["high"].max())
        low_7d   = float(df["low"].min())
        qvol_24h = float(df_24h["quote_volume"].sum()) if "quote_volume" in df_24h.columns else 0.0
        chg_pct  = (price - open_24h) / open_24h * 100 if open_24h else 0.0
        chg_emoji= "🟢" if chg_pct >= 0 else "🔴"

        if qvol_24h >= 1_000_000_000:
            vol_str = f"${qvol_24h/1e9:.2f}B"
        elif qvol_24h >= 1_000_000:
            vol_str = f"${qvol_24h/1e6:.1f}M"
        else:
            vol_str = f"${qvol_24h:,.0f}"

        sig    = get_latest_signal(sym)
        signal = sig.get("signal", "—") if sig else "—"
        regime = sig.get("market_regime", "—") if sig else "—"
        conf   = sig.get("confidence", 0) if sig else 0
        sig_emoji = {"BUY": "🟢", "SELL": "🔴", "HOLD": "🟡"}.get(signal, "⚪")

        # Open live trade
        trades = [t for t in self._executor.all_open_trades() if t.get("symbol") == sym]
        if trades:
            t     = trades[0]
            dir_  = t.get("direction", "?")
            entry = float(t.get("entry_price") or 0)
            sl    = float(t.get("stop_loss") or 0)
            tp    = float(t.get("take_profit") or 0)
            size  = float(t.get("position_size") or 0)
            lev   = int(t.get("leverage") or 1)
            rem   = float(t.get("remaining_size") or size)
            pnl   = (price-entry)*rem if dir_=="BUY" else (entry-price)*rem
            lev_s = f" {lev}×" if lev > 1 else ""
            dir_e = "🟢" if dir_=="BUY" else "🔴"
            trade_s = (
                f"\n📌 <b>Open Live Trade</b>\n"
                f"  {dir_e} {dir_}{lev_s} @ <code>{entry:.4f}</code>\n"
                f"  SL: <code>{sl:.4f}</code>  TP: <code>{tp:.4f}</code>\n"
                f"  uPnL: <b>{'+'if pnl>=0 else ''}{pnl:.2f}</b>"
            )
        else:
            trade_s = "\n📌 <b>No open live trade</b>"

        # Performance from live_trades
        with get_conn() as conn:
            perf = dict(conn.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN CAST(pnl AS REAL)>0 THEN 1 ELSE 0 END) AS wins,
                       SUM(CAST(pnl AS REAL)) AS total_pnl
                FROM live_trades WHERE symbol=? AND status!='open'
                """,
                (sym,),
            ).fetchone() or {})

        total = int(perf.get("total") or 0)
        wins  = int(perf.get("wins") or 0)
        t_pnl = float(perf.get("total_pnl") or 0)
        wr    = (wins / total * 100) if total else 0

        lines = [
            f"🪙 <b>{sym}</b>  [LIVE]",
            f"",
            f"<b>Price</b>",
            f"  Now:     <code>{price:.4f}</code>",
            f"  24h Chg: {chg_emoji} {chg_pct:+.2f}%",
            f"  24h H/L: <code>{high_24h:.4f}</code> / <code>{low_24h:.4f}</code>",
            f"  7d  H/L: <code>{high_7d:.4f}</code> / <code>{low_7d:.4f}</code>",
            f"  24h Vol: {vol_str}",
            f"",
            f"<b>Strategy</b>",
            f"  Regime:  {regime}",
            f"  Signal:  {sig_emoji} <b>{signal}</b>  conf={conf:.0f}",
            trade_s,
            f"",
            f"📊 <b>Live Performance</b>  ({total} trades)",
            f"  WR={wr:.1f}%  PnL={'+'if t_pnl>=0 else ''}{t_pnl:.2f}" if total else "  No closed trades yet.",
        ]
        self._send(chat_id, "\n".join(lines))
