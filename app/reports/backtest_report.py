"""Detailed backtest report renderer."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from app.data.repository import get_backtest_runs
from app.reports.reporter import _fmt_ts, _pnl_color, _pct_color

console = Console()


def print_detailed_backtest_report(symbol: str) -> None:
    runs = get_backtest_runs(symbol, limit=5)
    if not runs:
        console.print(f"[yellow]No backtest runs found for {symbol}[/yellow]")
        return

    for run in runs[:1]:  # Show the most recent
        m = run.get("metrics", {}) or {}

        console.print()
        console.print(Panel(
            f"[bold]Backtest Report — {symbol}[/bold]",
            subtitle=(
                f"Run #{run['id']}  |  "
                f"{_fmt_ts(run['start_time'])} → {_fmt_ts(run['end_time'])}  |  "
                f"Capital: ${run['initial_capital']:,.0f}"
            ),
            border_style="blue",
        ))

        # Performance summary
        perf = Table(title="Performance Summary", box=box.SIMPLE_HEAD)
        perf.add_column("Metric")
        perf.add_column("Value", justify="right")

        rows = [
            ("Initial Capital",       f"${run['initial_capital']:,.2f}"),
            ("Final Capital",         f"${m.get('final_capital', 0):,.2f}"),
            ("Net Profit",            _pnl_color(m.get("net_profit", 0))),
            ("Return %",              _pct_color(m.get("return_pct", 0))),
            ("Total Trades",          str(m.get("total_trades", 0))),
            ("Win / Loss",            f"{m.get('win_count', 0)} / {m.get('loss_count', 0)}"),
            ("Win Rate",              f"{m.get('win_rate', 0)*100:.1f}%"),
            ("Profit Factor",         f"{m.get('profit_factor', 0):.2f}"),
            ("Max Drawdown",          f"[red]{m.get('max_drawdown_pct', 0):.2f}%[/red]"),
            ("Expectancy",            f"${m.get('expectancy', 0):,.2f}"),
            ("Avg Win",               f"${m.get('avg_win', 0):,.2f}"),
            ("Avg Loss",              f"${m.get('avg_loss', 0):,.2f}"),
            ("Max Consec. Wins",      str(m.get("max_consecutive_wins", 0))),
            ("Max Consec. Losses",    str(m.get("max_consecutive_losses", 0))),
            ("Avg Trade Duration",    f"{m.get('avg_trade_duration_h', 0):.1f}h"),
            ("BUY Win Rate",          f"{m.get('buy_model_win_rate', 0)*100:.1f}%"),
            ("SELL Win Rate",         f"{m.get('sell_model_win_rate', 0)*100:.1f}%"),
        ]
        for label, value in rows:
            perf.add_row(label, value)

        console.print(perf)

        # Assessment
        pf = m.get("profit_factor", 0)
        wr = m.get("win_rate", 0)
        dd = m.get("max_drawdown_pct", 0)

        console.print("\n[bold]Assessment[/bold]")
        if pf >= 1.5 and wr >= 0.45 and dd <= 20:
            console.print("  [green]✓ Strategy shows positive expectancy[/green]")
        elif pf >= 1.0:
            console.print("  [yellow]⚠ Strategy is marginally profitable — needs improvement[/yellow]")
        else:
            console.print("  [red]✗ Strategy is unprofitable in this period[/red]")

        if dd > 25:
            console.print(f"  [red]✗ Drawdown {dd:.1f}% is too high — review risk management[/red]")

        if m.get("total_trades", 0) < 30:
            console.print("  [yellow]⚠ Low trade count — results may not be statistically significant[/yellow]")
