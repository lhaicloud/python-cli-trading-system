"""Self-learning report — model improvement, mistakes, recommendations."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from app.data.repository import (
    get_active_model,
    get_backtest_runs,
    get_feature_snapshots,
    get_paper_trade_summary,
)
from app.utils.logger import get_logger

console = Console()
logger  = get_logger(__name__)


def print_learning_report(symbol: str) -> None:
    console.print()
    console.print(Panel(
        f"[bold]Self-Learning Report — {symbol}[/bold]",
        subtitle="Model analysis, mistakes, and recommendations",
        border_style="magenta",
    ))

    snapshots = get_feature_snapshots(symbol)
    wins   = [s for s in snapshots if s.get("outcome") == "win"]
    losses = [s for s in snapshots if s.get("outcome") == "loss"]
    open_  = [s for s in snapshots if s.get("outcome") is None]

    total = len(wins) + len(losses)
    wr    = len(wins) / total * 100 if total > 0 else 0.0

    console.print(f"\n[bold]Labelled Trades[/bold]")
    console.print(f"  Total: {total}  Wins: {len(wins)}  Losses: {len(losses)}  "
                  f"Open (unlabelled): {len(open_)}")
    console.print(f"  Overall win rate: [bold]{wr:.1f}%[/bold]")

    # Analyse loss patterns
    console.print(f"\n[bold]Loss Analysis[/bold]")
    if losses:
        loss_features = [s.get("features", {}) for s in losses]
        regimes   = [f.get("market_regime", "unknown") for f in loss_features]
        zones     = [f.get("zone_score", 0) for f in loss_features]
        rrs       = [f.get("risk_reward", 0) for f in loss_features]
        sweeps    = [f.get("liquidity_sweep", 0) for f in loss_features]

        from collections import Counter
        regime_counts = Counter(regimes)
        console.print(f"  Regime at loss: {dict(regime_counts.most_common(3))}")
        avg_zone_loss = sum(zones) / len(zones) if zones else 0
        avg_rr_loss   = sum(rrs)   / len(rrs)   if rrs   else 0
        no_sweep_pct  = (1 - sum(sweeps) / len(sweeps)) * 100 if sweeps else 0
        console.print(f"  Avg zone score on losses: {avg_zone_loss:.1f}")
        console.print(f"  Avg R:R on losses:        {avg_rr_loss:.2f}")
        console.print(f"  Losses without liq sweep: {no_sweep_pct:.0f}%")

        # Recommendations
        console.print("\n[bold]Recommendations[/bold]")
        if avg_zone_loss < 70:
            console.print("  [yellow]⚠[/yellow] Many losses from low-score zones. "
                          "Consider raising MIN_ZONE_SCORE to 70+")
        if no_sweep_pct > 60:
            console.print("  [yellow]⚠[/yellow] Most losses had no liquidity sweep. "
                          "Require sweep confirmation for all entries.")
        if avg_rr_loss < 2.0:
            console.print("  [yellow]⚠[/yellow] Low R:R on losses. "
                          "Only trade setups with R:R ≥ 2.5.")

        loss_regimes_bad = [r for r in regimes if "choppy" in r or "squeeze" in r]
        if len(loss_regimes_bad) > len(losses) * 0.3:
            console.print("  [yellow]⚠[/yellow] Many losses in choppy/squeeze regime. "
                          "Strengthen HOLD logic for these regimes.")
    else:
        console.print("  [dim]No labelled losses yet.[/dim]")

    # Model status
    console.print("\n[bold]Model Status[/bold]")
    for mt in ("buy", "sell"):
        mv = get_active_model(symbol, mt)
        if mv:
            status_color = {"active": "green", "candidate": "yellow",
                            "rejected": "red"}.get(mv.get("status", ""), "white")
            console.print(
                f"  {mt.upper()} model: [{status_color}]{mv.get('status', '?').upper()}[/{status_color}]  "
                f"version={mv['version']}  algo={mv.get('algorithm', '?')}"
            )
            if mv.get("status") != "active":
                console.print(f"    [dim]Model is not active — run: python main.py train --model {mt}[/dim]")
        else:
            console.print(f"  {mt.upper()} model: [red]NOT TRAINED[/red]")
            console.print(f"    [dim]Run: python main.py train --symbol {symbol} --model {mt}[/dim]")

    # Safety assessment
    console.print("\n[bold]Safety Assessment[/bold]")
    buy_mv  = get_active_model(symbol, "buy")
    sell_mv = get_active_model(symbol, "sell")
    runs    = get_backtest_runs(symbol, limit=1)

    has_backtest  = bool(runs)
    has_buy_model = bool(buy_mv)
    has_sell_model = bool(sell_mv)
    enough_data   = total >= 30

    if has_backtest and has_buy_model and has_sell_model and enough_data:
        console.print("  [green]OK System appears ready for continued paper trading[/green]")
    else:
        console.print("  [yellow]!! System is EXPERIMENTAL - not all components validated[/yellow]")
        if not has_backtest:
            console.print("  [red]XX[/red] No backtest run. Run: python main.py backtest --symbol BTCUSDT")
        if not has_buy_model:
            console.print("  [red]XX[/red] No BUY model. Run: python main.py train --symbol BTCUSDT --model buy")
        if not has_sell_model:
            console.print("  [red]XX[/red] No SELL model. Run: python main.py train --symbol BTCUSDT --model sell")
        if not enough_data:
            console.print(f"  [red]XX[/red] Only {total} labelled trades. Need 30+ for reliable training.")
