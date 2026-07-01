"""
Telegram notification client.

Uses the Telegram Bot API directly via httpx — no extra library needed.

Setup:
  1. Message @BotFather on Telegram → /newbot → copy the token
  2. Start a chat with your bot, then visit:
       https://api.telegram.org/bot<TOKEN>/getUpdates
     to find your chat_id (the "id" field inside "chat")
  3. Add to .env:
       TELEGRAM_BOT_TOKEN=123456:ABC-your-token
       TELEGRAM_CHAT_ID=987654321
"""

from __future__ import annotations

import httpx
from app.config import get_settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

_BASE = "https://api.telegram.org"

# Emoji shortcuts that render on all Telegram clients
_SIGNAL_EMOJI = {"BUY": "\U0001f7e2", "SELL": "\U0001f534", "HOLD": "\U0001f7e1"}  # 🟢 🔴 🟡


def send_message(text: str, parse_mode: str = "HTML") -> bool:
    """
    Send a plain text message to the configured Telegram chat.

    Returns True on success, False on failure (never raises).
    """
    cfg = get_settings()
    token   = getattr(cfg, "telegram_bot_token", "")
    chat_id = getattr(cfg, "telegram_chat_id", "")

    if not token or not chat_id:
        logger.debug("Telegram not configured — skipping notification")
        return False

    url = f"{_BASE}/bot{token}/sendMessage"
    payload = {
        "chat_id":    chat_id,
        "text":       text,
        "parse_mode": parse_mode,
    }
    try:
        resp = httpx.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            return True
        logger.warning("Telegram API error %d: %s", resp.status_code, resp.text[:200])
        return False
    except Exception as exc:
        logger.warning("Telegram send failed: %s", exc)
        return False


def notify_signal(sig, leverage: int | None = None) -> bool:
    """Format and send a signal notification."""
    emoji  = _SIGNAL_EMOJI.get(sig.signal, "⚪")
    signal_line = f"{emoji} <b>{sig.signal}</b>  Confidence: {sig.confidence:.0f}/100"

    lines = [
        f"<b>LQ-MTF Signal — {sig.symbol}</b>",
        "",
        signal_line,
        f"Regime:   {sig.market_regime or '—'}",
        f"4H Bias:  {sig.h4_bias or '—'}",
        f"1H Conf:  {sig.h1_confirmation or '—'}",
        f"Zone:     {sig.m30_zone_type or '—'}  score={sig.zone_score:.0f}  ({sig.zone_rating or '—'})",
        f"Liq sweep: {'YES' if sig.liquidity_sweep else 'NO'}",
        f"Location: {sig.premium_discount or '—'}",
    ]

    if sig.signal != "HOLD":
        lev_str = f"  Leverage: <b>{leverage}×</b>" if leverage and leverage > 1 else ""
        lines += [
            "",
            f"Entry:  <code>{sig.entry_price:.4f}</code>",
            f"SL:     <code>{sig.stop_loss:.4f}</code>",
            f"TP:     <code>{sig.take_profit:.4f}</code>",
            f"R:R:    1 : {sig.risk_reward:.2f}{lev_str}",
            f"Setup:  {sig.setup_type or '—'}",
        ]

    if sig.warnings:
        lines += ["", "⚠️ " + " | ".join(sig.warnings[:2])]

    from app.utils.timeframes import ms_to_dt
    lines.append(f"\n<i>{ms_to_dt(sig.timestamp).strftime('%Y-%m-%d %H:%M UTC')}</i>")

    return send_message("\n".join(lines))


def notify_trade_opened(trade: dict, symbol: str, leverage: int = 1) -> bool:
    """Notify when a paper trade is opened."""
    direction = trade.get("direction", "?")
    emoji     = "\U0001f7e2" if direction == "BUY" else "\U0001f534"
    lev_str   = f"\nLeverage: <b>{leverage}×</b>" if leverage > 1 else ""
    text = (
        f"{emoji} <b>Paper Trade OPENED — {symbol}</b>\n\n"
        f"Direction: <b>{direction}</b>\n"
        f"Entry:  <code>{trade.get('entry_price', 0):.4f}</code>\n"
        f"SL:     <code>{trade.get('stop_loss', 0):.4f}</code>\n"
        f"TP:     <code>{trade.get('take_profit', 0):.4f}</code>\n"
        f"R:R:    1 : {trade.get('risk_reward', 0):.2f}{lev_str}\n"
        f"Size:   {trade.get('position_size', 0):.6f} units"
    )
    return send_message(text)


def notify_trade_closed(trade: dict, symbol: str, leverage: int = 1) -> bool:
    """Notify when a paper trade is closed (SL or TP hit)."""
    pnl     = trade.get("pnl", 0)
    status  = trade.get("status", "closed")
    emoji   = "\U0001f4b0" if pnl > 0 else "\U0001f4c9"  # 💰 📉
    result  = "WIN" if pnl > 0 else "LOSS"
    pnl_pct = trade.get("pnl_pct", 0)
    mfe     = float(trade.get("max_favorable_excursion") or 0)
    mae     = float(trade.get("max_adverse_excursion") or 0)

    lev_str = f"\nLeverage: {leverage}×" if leverage > 1 else ""
    mfe_mae = f"\nMFE: {mfe:.4f}  MAE: {mae:.4f}" if mfe or mae else ""

    text = (
        f"{emoji} <b>Paper Trade CLOSED — {symbol}</b>\n\n"
        f"Result:    <b>{result}</b>  ({status.replace('_', ' ').upper()})\n"
        f"Direction: {trade.get('direction', '?')}{lev_str}\n"
        f"Entry:  <code>{trade.get('entry_price', 0):.4f}</code>\n"
        f"Exit:   <code>{trade.get('exit_price', trade.get('close_price', 0)):.4f}</code>\n"
        f"PnL:    <b>{'+'if pnl >= 0 else ''}{pnl:.2f}</b> ({pnl_pct:+.2f}%)"
        f"{mfe_mae}"
    )
    return send_message(text)


def notify_ratchet_update(trade: dict, symbol: str, new_sl: float, level: int) -> bool:
    """Notify when the trailing stop ratchets to a new level."""
    labels = {1: "Breakeven", 2: "Lock 0.75R", 3: "Lock 1.5R", 4: "ATR Trail"}
    text = (
        f"\U0001f512 <b>Stop Ratcheted — {symbol}</b>\n\n"
        f"Level:  <b>{labels.get(level, level)}</b>\n"
        f"New SL: <code>{new_sl:.6f}</code>\n"
        f"Entry:  <code>{trade.get('entry_price', 0):.4f}</code>"
    )
    return send_message(text)


def notify_error(context: str, error: str) -> bool:
    text = f"⚠️ <b>LQ-MTF Error</b>\n\n<i>{context}</i>\n\n<code>{error[:300]}</code>"
    return send_message(text)


def notify_heartbeat(symbol: str, price: float, regime: str, candle_time: str) -> bool:
    text = (
        f"\U0001f916 <b>LQ-MTF Heartbeat — {symbol}</b>\n"
        f"Price:   <code>{price:.2f}</code>\n"
        f"Regime:  {regime}\n"
        f"Candle:  {candle_time}"
    )
    return send_message(text)


def answer_callback_query(callback_query_id: str) -> bool:
    """Acknowledge a Telegram inline button tap (clears the loading spinner)."""
    cfg   = get_settings()
    token = getattr(cfg, "telegram_bot_token", "")
    if not token:
        return False
    try:
        resp = httpx.post(
            f"{_BASE}/bot{token}/answerCallbackQuery",
            json={"callback_query_id": callback_query_id},
            timeout=5,
        )
        return resp.status_code == 200
    except Exception as exc:
        logger.debug("answerCallbackQuery failed: %s", exc)
        return False


def send_inline_menu(text: str, buttons: list[list[dict]]) -> bool:
    """Send a message with an inline keyboard. buttons is a list of rows, each row a list of dicts with 'text' and 'callback_data'."""
    cfg     = get_settings()
    token   = getattr(cfg, "telegram_bot_token", "")
    chat_id = getattr(cfg, "telegram_chat_id", "")
    if not token or not chat_id:
        return False
    payload = {
        "chat_id":      chat_id,
        "text":         text,
        "parse_mode":   "HTML",
        "reply_markup": {"inline_keyboard": buttons},
    }
    try:
        resp = httpx.post(f"{_BASE}/bot{token}/sendMessage", json=payload, timeout=10)
        return resp.status_code == 200
    except Exception as exc:
        logger.warning("send_inline_menu failed: %s", exc)
        return False
