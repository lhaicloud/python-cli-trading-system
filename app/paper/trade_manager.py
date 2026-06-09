"""Paper trade management — one-shot session for the `paper` CLI command."""

from __future__ import annotations

from app.data.binance_client import BinanceClient
from app.data.repository import get_paper_trade_summary
from app.paper.account import get_paper_capital, update_capital_after_trade
from app.paper.position_manager import PositionManager
from app.paper.signal_filter import SignalFilter
from app.ta.signals import generate_signal, SignalResult
from app.utils.logger import get_logger

logger  = get_logger(__name__)
_filter = SignalFilter()


def run_paper_session(
    symbol: str,
    df_1d,
    df_12h,
    df_4h,
    df_1h,
    df_30m,
    df_1w,
    capital: float | None = None,
    risk_pct: float = 1.0,
    signal_id: int | None = None,
) -> dict:
    """
    Run one paper trading cycle:
      1. Fetch current price.
      2. Update open trades (SL/TP check).
      3. Generate signal.
      4. Apply SignalFilter.
      5. Open trade if signal passes.

    Returns summary dict.
    """
    if capital is None:
        capital = get_paper_capital(symbol)

    pm = PositionManager(symbols=[symbol], risk_pct=risk_pct)

    # Fetch live price; fall back to last candle close
    try:
        with BinanceClient() as client:
            current_price = client.get_ticker_price(symbol)
    except Exception as exc:
        logger.error("[Paper] Could not fetch price: %s", exc)
        current_price = float(df_30m["close"].iloc[-1]) if not df_30m.empty else 0.0

    # Update open trades
    closed = pm.check_price(symbol, current_price)
    for t in closed:
        pnl     = t.get("pnl", 0)
        capital = update_capital_after_trade(symbol, pnl)

    # Generate signal
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

    trade_id = None
    if sig.signal in ("BUY", "SELL"):
        passed, reason = _filter.evaluate(sig)
        if passed:
            trade_id = pm.open(symbol, sig, capital, signal_id)
        else:
            logger.info("[Paper][%s] Signal filtered: %s", symbol, reason)

    summary = get_paper_trade_summary(symbol)
    return {
        "signal":        sig,
        "current_price": current_price,
        "capital":       capital,
        "trade_opened":  trade_id,
        "closed_trades": closed,
        "summary":       summary,
    }
