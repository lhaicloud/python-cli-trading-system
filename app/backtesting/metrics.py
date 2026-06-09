"""Backtest performance metrics calculation."""

from __future__ import annotations

import numpy as np


def compute_metrics(trades: list[dict], initial_capital: float) -> dict:
    """
    Compute full backtest metrics from a list of closed trade dicts.

    Each trade must have: pnl, pnl_pct, direction, exit_reason,
                          entry_time, exit_time, signal_confidence.
    """
    if not trades:
        return _empty_metrics(initial_capital)

    pnls = [t["pnl"] for t in trades]
    pnl_pcts = [t.get("pnl_pct", 0) for t in trades]

    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    total_trades = len(trades)
    win_count    = len(wins)
    loss_count   = len(losses)
    win_rate     = win_count / total_trades if total_trades > 0 else 0.0

    gross_profit = sum(wins) if wins else 0.0
    gross_loss   = abs(sum(losses)) if losses else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    net_profit    = sum(pnls)
    avg_win       = np.mean(wins) if wins else 0.0
    avg_loss      = abs(np.mean(losses)) if losses else 0.0
    expectancy    = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)

    # Max drawdown
    equity = [initial_capital]
    for p in pnls:
        equity.append(equity[-1] + p)
    equity_arr = np.array(equity)
    peaks = np.maximum.accumulate(equity_arr)
    drawdowns = (equity_arr - peaks) / peaks
    max_drawdown = float(abs(drawdowns.min()))

    final_capital = equity[-1]

    # Consecutive wins/losses
    max_consec_wins   = _max_consecutive(pnls, positive=True)
    max_consec_losses = _max_consecutive(pnls, positive=False)

    # Average trade duration (ms)
    durations = [
        t.get("exit_time", 0) - t.get("entry_time", 0)
        for t in trades
        if t.get("exit_time") and t.get("entry_time")
    ]
    avg_duration_h = (np.mean(durations) / 3_600_000) if durations else 0.0

    # Signals breakdown
    buy_trades  = [t for t in trades if t.get("direction") == "BUY"]
    sell_trades = [t for t in trades if t.get("direction") == "SELL"]
    buy_wr  = sum(1 for t in buy_trades  if t["pnl"] > 0) / len(buy_trades)  if buy_trades  else 0.0
    sell_wr = sum(1 for t in sell_trades if t["pnl"] > 0) / len(sell_trades) if sell_trades else 0.0

    # Ratchet metrics
    ratchet_exits = sum(1 for t in trades if t.get("exit_reason") == "ratchet_sl")
    ratchet_exit_pct = round(ratchet_exits / total_trades * 100, 1) if total_trades > 0 else 0.0

    return {
        "total_trades":        total_trades,
        "win_count":           win_count,
        "loss_count":          loss_count,
        "win_rate":            round(win_rate, 4),
        "loss_rate":           round(1 - win_rate, 4),
        "net_profit":          round(net_profit, 2),
        "gross_profit":        round(gross_profit, 2),
        "gross_loss":          round(gross_loss, 2),
        "profit_factor":       round(profit_factor, 3) if profit_factor != float("inf") else 999.0,
        "avg_win":             round(avg_win, 2),
        "avg_loss":            round(avg_loss, 2),
        "expectancy":          round(expectancy, 2),
        "max_drawdown_pct":    round(max_drawdown * 100, 2),
        "final_capital":       round(final_capital, 2),
        "return_pct":          round(net_profit / initial_capital * 100, 2),
        "max_consecutive_wins":   max_consec_wins,
        "max_consecutive_losses": max_consec_losses,
        "avg_trade_duration_h":   round(avg_duration_h, 1),
        "buy_model_win_rate":     round(buy_wr, 4),
        "sell_model_win_rate":    round(sell_wr, 4),
        "long_win_rate":          round(buy_wr, 4),
        "short_win_rate":         round(sell_wr, 4),
        "ratchet_exits":          ratchet_exits,
        "ratchet_exit_pct":       ratchet_exit_pct,
    }


def _max_consecutive(pnls: list[float], positive: bool) -> int:
    max_run = 0
    current = 0
    for p in pnls:
        if (positive and p > 0) or (not positive and p <= 0):
            current += 1
            max_run = max(max_run, current)
        else:
            current = 0
    return max_run


def _empty_metrics(initial_capital: float) -> dict:
    return {
        "total_trades": 0,
        "win_count": 0,
        "loss_count": 0,
        "win_rate": 0.0,
        "loss_rate": 0.0,
        "net_profit": 0.0,
        "gross_profit": 0.0,
        "gross_loss": 0.0,
        "profit_factor": 0.0,
        "avg_win": 0.0,
        "avg_loss": 0.0,
        "expectancy": 0.0,
        "max_drawdown_pct": 0.0,
        "final_capital": initial_capital,
        "return_pct": 0.0,
        "max_consecutive_wins": 0,
        "max_consecutive_losses": 0,
        "avg_trade_duration_h": 0.0,
        "buy_model_win_rate": 0.0,
        "sell_model_win_rate": 0.0,
        "long_win_rate": 0.0,
        "short_win_rate": 0.0,
        "ratchet_exits": 0,
        "ratchet_exit_pct": 0.0,
    }
