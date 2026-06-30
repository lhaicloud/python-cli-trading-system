"""
Live channel notification helpers.

Mirrors app/notifications/telegram.py in function signatures but sends to
the LIVE Telegram channel (LIVE_TELEGRAM_TOKEN + LIVE_TELEGRAM_CHAT_ID).
All messages are prefixed with 🔴 [LIVE]. Paper channel is never touched.
"""

from __future__ import annotations

import httpx
from app.config import get_settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

_BASE = "https://api.telegram.org"


def live_send_message(text: str, parse_mode: str = "HTML") -> bool:
    cfg     = get_settings()
    token   = getattr(cfg, "live_telegram_token", "")
    chat_id = getattr(cfg, "live_telegram_chat_id", "")

    if not token or not chat_id:
        logger.debug("[Live Telegram] Not configured — skipping")
        return False

    try:
        resp = httpx.post(
            f"{_BASE}/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode},
            timeout=10,
        )
        if resp.status_code == 200:
            return True
        logger.warning("[Live Telegram] API error %d: %s", resp.status_code, resp.text[:200])
        return False
    except Exception as exc:
        logger.warning("[Live Telegram] send failed: %s", exc)
        return False


def live_send_inline_menu(text: str, buttons: list[list[dict]]) -> bool:
    cfg     = get_settings()
    token   = getattr(cfg, "live_telegram_token", "")
    chat_id = getattr(cfg, "live_telegram_chat_id", "")
    if not token or not chat_id:
        return False
    try:
        resp = httpx.post(
            f"{_BASE}/bot{token}/sendMessage",
            json={
                "chat_id":      chat_id,
                "text":         text,
                "parse_mode":   "HTML",
                "reply_markup": {"inline_keyboard": buttons},
            },
            timeout=10,
        )
        return resp.status_code == 200
    except Exception as exc:
        logger.warning("[Live Telegram] send_inline_menu failed: %s", exc)
        return False


def live_answer_callback(token: str, callback_query_id: str) -> bool:
    try:
        resp = httpx.post(
            f"{_BASE}/bot{token}/answerCallbackQuery",
            json={"callback_query_id": callback_query_id},
            timeout=5,
        )
        return resp.status_code == 200
    except Exception as exc:
        logger.debug("[Live Telegram] answerCallbackQuery failed: %s", exc)
        return False


# ── Notification functions ────────────────────────────────────────────────────

def live_notify_signal(sig, leverage: int = 1) -> bool:
    _EMOJI = {"BUY": "\U0001f7e2", "SELL": "\U0001f534", "HOLD": "\U0001f7e1"}
    emoji  = _EMOJI.get(sig.signal, "⚪")

    lines = [
        f"\U0001f534 [LIVE] <b>Signal — {sig.symbol}</b>",
        "",
        f"{emoji} <b>{sig.signal}</b>  Confidence: {sig.confidence:.0f}/100",
        f"Regime:   {sig.market_regime or '—'}",
        f"4H Bias:  {sig.h4_bias or '—'}",
        f"1H Conf:  {sig.h1_confirmation or '—'}",
        f"Zone:     {sig.m30_zone_type or '—'}  score={sig.zone_score:.0f}",
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
    return live_send_message("\n".join(lines))


def live_notify_trade_opened(trade: dict, symbol: str, leverage: int = 1) -> bool:
    direction = trade.get("direction", "?")
    emoji     = "\U0001f7e2" if direction == "BUY" else "\U0001f534"
    lev_str   = f"\nLeverage: <b>{leverage}×</b>" if leverage > 1 else ""
    text = (
        f"\U0001f534 [LIVE] {emoji} <b>Trade OPENED — {symbol}</b>\n\n"
        f"Direction: <b>{direction}</b>\n"
        f"Entry:  <code>{trade.get('entry_price', 0):.4f}</code>\n"
        f"SL:     <code>{trade.get('stop_loss', 0):.4f}</code>\n"
        f"TP:     <code>{trade.get('take_profit', 0):.4f}</code>\n"
        f"R:R:    1 : {trade.get('risk_reward', 0):.2f}{lev_str}\n"
        f"Size:   {trade.get('position_size', 0):.6f} units\n"
        f"Partial TP: <code>{trade.get('partial_tp_price', 0):.4f}</code> (+1.5R)"
    )
    return live_send_message(text)


def live_notify_trade_closed(trade: dict, symbol: str, leverage: int = 1) -> bool:
    pnl     = float(trade.get("pnl") or 0)
    status  = trade.get("status", "closed")
    emoji   = "\U0001f4b0" if pnl > 0 else "\U0001f4c9"
    result  = "WIN" if pnl > 0 else "LOSS"
    pnl_pct = float(trade.get("pnl_pct") or 0)
    mfe     = float(trade.get("max_favorable_excursion") or 0)
    mae     = float(trade.get("max_adverse_excursion") or 0)
    lev_str = f"\nLeverage: {leverage}×" if leverage > 1 else ""
    mfe_mae = f"\nMFE: {mfe:.4f}  MAE: {mae:.4f}" if mfe or mae else ""
    text = (
        f"\U0001f534 [LIVE] {emoji} <b>Trade CLOSED — {symbol}</b>\n\n"
        f"Result:    <b>{result}</b>  ({status.replace('_', ' ').upper()})\n"
        f"Direction: {trade.get('direction', '?')}{lev_str}\n"
        f"Entry:  <code>{trade.get('entry_price', 0):.4f}</code>\n"
        f"Exit:   <code>{trade.get('close_price', 0):.4f}</code>\n"
        f"PnL:    <b>{'+'if pnl >= 0 else ''}{pnl:.2f}</b> ({pnl_pct:+.2f}%)"
        f"{mfe_mae}"
    )
    return live_send_message(text)


def live_notify_partial_tp(
    trade: dict,
    symbol: str,
    fill_price: float,
    partial_pnl: float,
) -> bool:
    remaining = float(trade.get("remaining_size") or 0)
    final_tp  = float(trade.get("take_profit") or 0)
    entry     = float(trade.get("entry_price") or 0)
    text = (
        f"\U0001f534 [LIVE] \U0001f3af <b>Partial TP Hit — {symbol}</b>\n\n"
        f"Filled @ <code>{fill_price:.4f}</code>\n"
        f"Partial PnL: <b>{'+'if partial_pnl >= 0 else ''}{partial_pnl:.2f}</b>\n"
        f"New SL: <code>{entry:.4f}</code> (break-even)\n"
        f"Remaining: {remaining:.6f} units\n"
        f"Final TP:  <code>{final_tp:.4f}</code>"
    )
    return live_send_message(text)


def live_notify_error(context: str, error: str) -> bool:
    text = (
        f"\U0001f534 [LIVE] ⚠️ <b>Error — {context}</b>\n\n"
        f"<code>{error[:400]}</code>"
    )
    return live_send_message(text)


def live_send_heartbeat(
    candle_dt: str,
    cycle_signals: list,
    open_trades: list,
    balance: float,
) -> bool:
    lines = [
        f"\U0001f534 [LIVE] \U0001f493 <b>Heartbeat — {candle_dt}</b>",
        f"Balance: <b>${balance:,.2f} USDT</b>",
    ]
    if open_trades:
        lines.append("\n<b>Open Positions:</b>")
        for t in open_trades:
            sym   = t.get("symbol", "?")
            dir_  = t.get("direction", "?")
            entry = float(t.get("entry_price") or 0)
            lev   = int(t.get("leverage") or 1)
            lev_s = f" {lev}×" if lev > 1 else ""
            lines.append(f"  {sym} {dir_}{lev_s} @ {entry:.4f}")
    else:
        lines.append("No open positions.")

    if cycle_signals:
        lines.append("\n<b>Latest Signals:</b>")
        for s in cycle_signals[:5]:
            sym    = s.get("symbol", "?")
            signal = s.get("signal", "—")
            conf   = s.get("confidence", 0)
            emoji  = {"BUY": "🟢", "SELL": "🔴", "HOLD": "🟡"}.get(signal, "⚪")
            lines.append(f"  {emoji} {sym}: {signal} ({conf:.0f})")

    return live_send_message("\n".join(lines))


def live_send_daily_digest(
    open_trades: list,
    balance: float,
    pnl_24h: float,
    stale_symbols: list,
) -> bool:
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    pnl_sign = "+" if pnl_24h >= 0 else ""
    pnl_emoji = "\U0001f4b0" if pnl_24h >= 0 else "\U0001f4c9"

    lines = [
        f"\U0001f534 [LIVE] \U0001f4c5 <b>Daily Digest — {today}</b>",
        f"",
        f"USDT Balance: <b>${balance:,.2f}</b>",
        f"24h PnL: {pnl_emoji} <b>{pnl_sign}{pnl_24h:.2f}</b>",
    ]

    if open_trades:
        lines.append(f"\n<b>Open Positions ({len(open_trades)}):</b>")
        for t in open_trades:
            sym   = t.get("symbol", "?")
            dir_  = t.get("direction", "?")
            entry = float(t.get("entry_price") or 0)
            lev   = int(t.get("leverage") or 1)
            lev_s = f" {lev}×" if lev > 1 else ""
            age_ms = int(t.get("open_time") or 0)
            age_h  = (
                (int(__import__("time").time() * 1000) - age_ms) / 3_600_000
                if age_ms else 0
            )
            lines.append(
                f"  {sym} {dir_}{lev_s} @ {entry:.4f}  age={age_h:.1f}h"
            )
    else:
        lines.append("\nNo open positions.")

    if stale_symbols:
        lines.append(f"\n⚠️ Stale candles: {', '.join(stale_symbols)}")

    return live_send_message("\n".join(lines))
