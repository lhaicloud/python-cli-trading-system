"""CLI report renderer using Rich tables."""

from __future__ import annotations

from datetime import datetime, timezone

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from app.data.repository import (
    get_active_model,
    get_backtest_runs,
    get_latest_data_quality,
    get_latest_signal,
    get_paper_trade_summary,
)
from app.ta.signals import SignalResult
from app.utils.timeframes import ms_to_dt

console = Console()


def print_signal_report(sig: SignalResult) -> None:
    """Print a full signal report to the console."""
    color = {"BUY": "green", "SELL": "red", "HOLD": "yellow", "BLOCKED": "magenta"}.get(sig.signal, "white")

    console.print()
    console.print(Panel(
        f"[bold {color}]{sig.signal}[/bold {color}]  "
        f"Confidence: [bold]{sig.confidence:.0f}/100[/bold]",
        title=f"[bold]LQ-MTF Signal — {sig.symbol}[/bold]",
        subtitle=f"[dim]{ms_to_dt(sig.timestamp).strftime('%Y-%m-%d %H:%M UTC')}[/dim]",
        border_style=color,
    ))

    # MTF score table (shown when scores are available)
    long_score  = getattr(sig, "long_score", 0.0)
    short_score = getattr(sig, "short_score", 0.0)
    tf_scores   = getattr(sig, "tf_scores", [])
    if long_score or short_score or tf_scores:
        mt = Table(box=box.SIMPLE, show_header=True, padding=(0, 1), title="MTF Scores")
        mt.add_column("TF",     style="dim",   width=6)
        mt.add_column("Bull",   style="green", width=8)
        mt.add_column("Bear",   style="red",   width=8)
        mt.add_column("Weight", style="dim",   width=8)
        for s in tf_scores:
            mt.add_row(
                s.timeframe,
                f"{s.bull_score:.1f}",
                f"{s.bear_score:.1f}",
                f"{s.weight:.2f}",
            )
        mt.add_section()
        gap = abs(long_score - short_score)
        mt.add_row(
            "[bold]FINAL[/bold]",
            f"[bold green]{long_score:.1f}[/bold green]",
            f"[bold red]{short_score:.1f}[/bold red]",
            f"gap={gap:.1f}",
        )
        console.print(mt)

    if getattr(sig, "rejection_reason", None):
        console.print(f"  [magenta]Blocked:[/magenta] {sig.rejection_reason}")

    # Main attributes table
    t = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    t.add_column("Field",  style="dim", width=28)
    t.add_column("Value",  style="bold")

    def row(label: str, value: str) -> None:
        t.add_row(label, value)

    row("Symbol",                   sig.symbol)
    row("Signal",                   f"[{color}]{sig.signal}[/{color}]")
    row("Confidence",               f"{sig.confidence:.0f} / 100")
    row("Market Regime",            sig.market_regime or "—")
    row("Daily Bias",               sig.daily_bias or "—")
    row("4H Bias",                  sig.h4_bias or "—")
    row("1H Confirmation",          sig.h1_confirmation or "—")
    row("30M Zone Type",            sig.m30_zone_type or "—")
    row("Zone Score",               f"{sig.zone_score:.0f} / 100" if sig.zone_score else "—")
    row("Zone Rating",              sig.zone_rating or "—")
    row("Liquidity Sweep",          "YES ✓" if sig.liquidity_sweep else "NO")
    row("Premium/Discount",         sig.premium_discount or "—")
    row("Setup Type",               sig.setup_type or "—")

    if sig.signal in ("BUY", "SELL"):
        row("Entry Price",          f"{sig.entry_price:.4f}")
        row("Stop Loss",            f"{sig.stop_loss:.4f}")
        row("Take Profit",          f"{sig.take_profit:.4f}")
        row("Risk / Reward",        f"1 : {sig.risk_reward:.2f}")

    row("Model Version",            sig.model_version)
    row("Data Quality",             sig.data_quality)

    console.print(t)

    if sig.reasons:
        console.print("[bold]Reasons:[/bold]")
        for r in sig.reasons:
            console.print(f"  [green]✓[/green] {r}")

    if sig.warnings:
        console.print("[bold yellow]Warnings:[/bold yellow]")
        for w in sig.warnings:
            console.print(f"  [yellow]⚠[/yellow] {w}")


def print_backtest_report(symbol: str, run_id: int | None = None) -> None:
    runs = get_backtest_runs(symbol)
    if not runs:
        console.print(f"[yellow]No backtest runs found for {symbol}[/yellow]")
        return

    run = runs[0]
    m   = run.get("metrics", {}) or {}

    console.print()
    console.print(Panel(
        f"Backtest Results — [bold]{symbol}[/bold]",
        subtitle=f"[dim]Run #{run['id']} | {_fmt_ts(run['start_time'])} → {_fmt_ts(run['end_time'])}[/dim]",
        border_style="blue",
    ))

    t = Table(box=box.SIMPLE_HEAD)
    t.add_column("Metric",  style="dim")
    t.add_column("Value",   style="bold")

    t.add_row("Initial Capital",     f"${run['initial_capital']:,.2f}")
    t.add_row("Final Capital",       f"${m.get('final_capital', 0):,.2f}")
    t.add_row("Net Profit",          _pnl_color(m.get("net_profit", 0)))
    t.add_row("Return %",            _pct_color(m.get("return_pct", 0)))
    t.add_row("Total Trades",        str(m.get("total_trades", 0)))
    t.add_row("Win Rate",            f"{m.get('win_rate', 0)*100:.1f}%")
    t.add_row("Profit Factor",       f"{m.get('profit_factor', 0):.2f}")
    t.add_row("Max Drawdown",        f"[red]{m.get('max_drawdown_pct', 0):.1f}%[/red]")
    t.add_row("Avg Win",             f"${m.get('avg_win', 0):,.2f}")
    t.add_row("Avg Loss",            f"${m.get('avg_loss', 0):,.2f}")
    t.add_row("Expectancy",          f"${m.get('expectancy', 0):,.2f}")
    t.add_row("Max Consec. Wins",    str(m.get("max_consecutive_wins", 0)))
    t.add_row("Max Consec. Losses",  str(m.get("max_consecutive_losses", 0)))
    t.add_row("Avg Trade Duration",  f"{m.get('avg_trade_duration_h', 0):.1f}h")
    t.add_row("BUY Model Win Rate",  f"{m.get('buy_model_win_rate', 0)*100:.1f}%")
    t.add_row("SELL Model Win Rate", f"{m.get('sell_model_win_rate', 0)*100:.1f}%")

    console.print(t)


def print_general_report(symbol: str) -> None:
    console.print()
    console.print(Panel(f"[bold]LQ-MTF Strategy Report — {symbol}[/bold]", border_style="blue"))

    # Data quality
    console.print("\n[bold]Data Quality[/bold]")
    for tf in ("1d", "4h", "1h", "30m"):
        dq = get_latest_data_quality(symbol, tf)
        if dq:
            score = dq["quality_score"]
            color = "green" if score >= 95 else "yellow" if score >= 80 else "red"
            console.print(f"  {tf:>4s}  [{color}]{score:.1f}%[/{color}]  "
                          f"({dq['actual_candles']:,}/{dq['expected_candles']:,} candles)")
        else:
            console.print(f"  {tf:>4s}  [dim]No data[/dim]")

    # Latest signal
    sig = get_latest_signal(symbol)
    console.print("\n[bold]Latest Signal[/bold]")
    if sig:
        color = {"BUY": "green", "SELL": "red", "HOLD": "yellow"}.get(sig["signal"], "white")
        console.print(
            f"  [{color}]{sig['signal']}[/{color}]  "
            f"Confidence={sig.get('confidence', 0):.0f}  "
            f"Regime={sig.get('market_regime', '—')}  "
            f"Zone={sig.get('zone_rating', '—')}  "
            f"R:R={sig.get('risk_reward', 0):.2f}"
        )
    else:
        console.print("  [dim]No signals generated yet[/dim]")

    # Paper trading summary — all symbols combined
    summary = get_paper_trade_summary()
    console.print("\n[bold]Paper Trading — All Symbols[/bold]")
    if summary and summary.get("total"):
        total = summary["total"]
        wins  = summary["wins"] or 0
        losses = summary["losses"] or 0
        pnl   = summary["total_pnl"] or 0
        best  = summary["best_trade"] or 0
        worst = summary["worst_trade"] or 0
        wr    = wins / total * 100 if total > 0 else 0
        console.print(
            f"  Trades={total}  Wins={wins}  Losses={losses}  "
            f"Win Rate={wr:.1f}%  Net PnL={_pnl_color(pnl)}"
        )
        console.print(
            f"  Best={_pnl_color(best)}  Worst={_pnl_color(worst)}"
        )

        # Per-symbol breakdown
        from app.data.repository import get_paper_trade_summary as _pts
        from app.db.connection import get_conn as _gc
        with _gc() as _c:
            syms = [r[0] for r in _c.execute(
                "SELECT DISTINCT symbol FROM paper_trades WHERE status != 'open' ORDER BY symbol"
            ).fetchall()]
        rows_data = []
        for s in syms:
            sm = _pts(s)
            if sm and sm.get("total"):
                t = sm["total"]; w = sm["wins"] or 0
                rows_data.append((s, t, w, sm["total_pnl"] or 0))
        if rows_data:
            sym_t = Table(box=box.SIMPLE, show_header=True, header_style="dim")
            sym_t.add_column("Symbol", style="bold")
            sym_t.add_column("Trades", justify="right")
            sym_t.add_column("WR%", justify="right")
            sym_t.add_column("Net PnL", justify="right")
            for s, t, w, p in rows_data:
                wr_s = f"{w/t*100:.0f}%" if t else "—"
                sym_t.add_row(s, str(t), wr_s, _pnl_color(p))
            console.print(sym_t)
    else:
        console.print("  [dim]No completed paper trades yet[/dim]")

    # Model status
    console.print("\n[bold]Models[/bold]")
    for mt in ("buy", "sell"):
        mv = get_active_model(symbol, mt)
        if mv:
            console.print(f"  {mt.upper()} model: [green]Active[/green]  version={mv['version']}")
        else:
            console.print(f"  {mt.upper()} model: [yellow]Not trained[/yellow]")

    # Backtest history
    runs = get_backtest_runs(symbol, limit=3)
    console.print("\n[bold]Recent Backtests[/bold]")
    if runs:
        bt = Table(box=box.SIMPLE)
        bt.add_column("Run #")
        bt.add_column("Period")
        bt.add_column("Trades")
        bt.add_column("Win Rate")
        bt.add_column("PF")
        bt.add_column("Net P&L")
        bt.add_column("MaxDD")
        for r in runs:
            m = r.get("metrics", {}) or {}
            bt.add_row(
                str(r["id"]),
                f"{_fmt_ts(r['start_time'])} — {_fmt_ts(r['end_time'])}",
                str(m.get("total_trades", 0)),
                f"{m.get('win_rate', 0)*100:.1f}%",
                f"{m.get('profit_factor', 0):.2f}",
                _pnl_color(m.get("net_profit", 0)),
                f"{m.get('max_drawdown_pct', 0):.1f}%",
            )
        console.print(bt)
    else:
        console.print("  [dim]No backtests run yet[/dim]")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pnl_color(val: float) -> str:
    color = "green" if val >= 0 else "red"
    return f"[{color}]${val:,.2f}[/{color}]"


def _pct_color(val: float) -> str:
    color = "green" if val >= 0 else "red"
    return f"[{color}]{val:.2f}%[/{color}]"


def _fmt_ts(ms: int | None) -> str:
    if not ms:
        return "—"
    return ms_to_dt(ms).strftime("%Y-%m-%d")
