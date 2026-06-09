"""
Walk-forward validation — run independent backtests on sequential time windows.

Shows whether strategy performance is consistent across different market
conditions or only works in specific regimes/periods.
"""

from __future__ import annotations

import datetime
from typing import Any

import numpy as np
from dateutil.relativedelta import relativedelta
from rich.console import Console

from app.backtesting.engine import run_backtest
from app.utils.logger import get_logger

logger  = get_logger(__name__)
console = Console()


def run_walk_forward(
    symbol: str,
    start_ms: int,
    end_ms: int,
    window_months: int = 3,
    initial_capital: float = 10_000.0,
    risk_pct: float = 1.0,
) -> dict[str, Any]:
    """
    Split [start_ms, end_ms] into equal windows and backtest each independently.

    Returns:
        windows : list of per-window result dicts
        summary : aggregate stats — win_rate mean/std, % profitable windows, etc.
    """
    windows = _build_windows(start_ms, end_ms, window_months)
    if len(windows) < 2:
        raise ValueError(
            f"Date range too short for {window_months}-month windows. "
            "Provide a range that covers at least 2 full windows."
        )

    results: list[dict] = []

    for i, (win_start, win_end) in enumerate(windows):
        label = f"{win_start.strftime('%Y-%m')} → {win_end.strftime('%Y-%m')}"
        console.print(
            f"  [dim]Window {i+1}/{len(windows)}: {label}[/dim]",
            highlight=False,
        )
        logger.info("[Walk-Forward] Window %d/%d: %s", i + 1, len(windows), label)

        win_start_ms = int(win_start.timestamp() * 1000)
        win_end_ms   = int(win_end.timestamp() * 1000)

        try:
            result = run_backtest(
                symbol=symbol,
                start_ms=win_start_ms,
                end_ms=win_end_ms,
                initial_capital=initial_capital,
                risk_pct=risk_pct,
            )
            m = result["metrics"]
            results.append({
                "period":           label,
                "start":            win_start.strftime("%Y-%m-%d"),
                "end":              win_end.strftime("%Y-%m-%d"),
                "total_trades":     m.get("total_trades", 0),
                "win_rate":         m.get("win_rate", 0.0),
                "profit_factor":    m.get("profit_factor", 0.0),
                "max_drawdown_pct": m.get("max_drawdown_pct", 0.0),
                "net_profit_pct":   m.get("return_pct", 0.0),
                "expectancy":       m.get("expectancy", 0.0),
                "error":            None,
            })
        except Exception as exc:
            logger.warning("[Walk-Forward] Window %s failed: %s", label, exc)
            results.append({
                "period":           label,
                "start":            win_start.strftime("%Y-%m-%d"),
                "end":              win_end.strftime("%Y-%m-%d"),
                "total_trades":     0,
                "win_rate":         0.0,
                "profit_factor":    0.0,
                "max_drawdown_pct": 0.0,
                "net_profit_pct":   0.0,
                "expectancy":       0.0,
                "error":            str(exc),
            })

    summary = _compute_summary(results)
    return {"windows": results, "summary": summary}


# ── Internal helpers ──────────────────────────────────────────────────────────

def _build_windows(
    start_ms: int,
    end_ms: int,
    window_months: int,
) -> list[tuple[datetime.datetime, datetime.datetime]]:
    """Return a list of (window_start, window_end) datetime pairs."""
    utc = datetime.timezone.utc
    start_dt = datetime.datetime.fromtimestamp(start_ms / 1000, tz=utc)
    end_dt   = datetime.datetime.fromtimestamp(end_ms   / 1000, tz=utc)

    windows: list[tuple[datetime.datetime, datetime.datetime]] = []
    cur = start_dt
    while cur < end_dt:
        win_end = min(cur + relativedelta(months=window_months), end_dt)
        if (win_end - cur).days < 14:  # skip slivers shorter than 2 weeks
            break
        windows.append((cur, win_end))
        cur = win_end
    return windows


def _compute_summary(results: list[dict]) -> dict[str, Any]:
    """Aggregate stats across windows that ran successfully with at least 1 trade."""
    valid = [r for r in results if r["error"] is None and r["total_trades"] > 0]
    summary: dict[str, Any] = {
        "total_windows": len(results),
        "valid_windows": len(valid),
    }
    if not valid:
        return summary

    win_rates = [r["win_rate"]         for r in valid]
    pfs       = [r["profit_factor"]    for r in valid]
    dds       = [r["max_drawdown_pct"] for r in valid]

    summary.update({
        "win_rate_mean":          round(float(np.mean(win_rates)),  4),
        "win_rate_std":           round(float(np.std(win_rates)),   4),
        "profitable_windows_pct": round(
            sum(1 for r in valid if r["profit_factor"] > 1.0) / len(valid) * 100, 1
        ),
        "worst_drawdown":         round(float(max(dds)),  2),
        "best_profit_factor":     round(float(max(pfs)),  3),
        "worst_profit_factor":    round(float(min(pfs)),  3),
        "total_trades":           sum(r["total_trades"] for r in valid),
    })
    return summary
