"""
LQ-MTF Strategy ??? CLI entry point.

Commands:
  backfill      Download historical candles
  update        Fetch latest candles
  analyze       Multi-timeframe market analysis
  signal        Generate BUY / SELL / HOLD signal
  backtest      Run historical backtest
  paper         Run paper trading cycle
  train         Train BUY or SELL model
  validate      Validate a candidate model
  report        Generate performance reports
  sync-universe Fetch top-N coins from Binance and backfill any not in DB
  live          Auto signal + paper trading watcher with Telegram alerts
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console

from app.db.migrations import run_migrations

app    = typer.Typer(
    name="lqmtf",
    help="LQ-MTF Strategy ??? Liquidity-Quality Multi-Timeframe Trading System",
    add_completion=False,
)
console = Console()

# ?????? Startup ?????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????

def _init() -> None:
    """Ensure DB is set up before any command runs."""
    run_migrations()


def _load_coin_pool() -> list[str] | None:
    """coin_pool.json's curated list, or None if the file isn't present."""
    path = Path("coin_pool.json")
    if not path.exists():
        return None
    with open(path) as f:
        return [s.upper() for s in json.load(f)["pool"]]


# ?????? backfill ??????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????

@app.command()
def backfill(
    symbol: str = typer.Option("BTCUSDT", help="Trading symbol"),
    timeframes: List[str] = typer.Option(["1d", "12h", "4h", "1h", "30m", "1w"], help="Timeframes to download"),
    start: str = typer.Option("2021-01-01", help="Start date YYYY-MM-DD"),
    end: str = typer.Option("", help="End date YYYY-MM-DD (defaults to today)"),
    no_resume: bool = typer.Option(False, "--no-resume", help="Start fresh, ignore existing data"),
) -> None:
    """Download historical OHLCV candle data from Binance."""
    _init()
    from app.data.backfill import backfill as _backfill
    from app.utils.timeframes import str_to_ms
    import time as _time

    start_ms = str_to_ms(start)
    end_ms   = str_to_ms(end) if end else int(_time.time() * 1000)

    console.print(f"\n[bold blue]Backfilling {symbol}[/bold blue] | "
                  f"TFs: {timeframes} | {start} ??? {end or 'now'}\n")
    try:
        reports = _backfill(
            symbol=symbol,
            timeframes=timeframes,
            start_ms=start_ms,
            end_ms=end_ms,
            resume=not no_resume,
        )
        console.print("\n[bold green]Backfill complete.[/bold green]")
    except Exception as exc:
        console.print(f"[red]ERROR: {exc}[/red]")
        sys.exit(1)


# ?????? update ????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????

@app.command()
def update(
    symbol: str = typer.Option("BTCUSDT", help="Trading symbol"),
    timeframes: List[str] = typer.Option(["1d", "12h", "4h", "1h", "30m", "1w"], help="Timeframes to update"),
) -> None:
    """Fetch the latest candles (incremental update)."""
    _init()
    from app.data.backfill import update_latest

    console.print(f"\n[bold blue]Updating {symbol}[/bold blue] | TFs: {timeframes}\n")
    try:
        counts = update_latest(symbol=symbol, timeframes=timeframes)
        total = sum(counts.values())
        console.print(f"\n[bold green]Update complete ??? {total} new candles inserted.[/bold green]")
    except Exception as exc:
        console.print(f"[red]ERROR: {exc}[/red]")
        sys.exit(1)


# ?????? analyze ?????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????

@app.command()
def analyze(
    symbol: str = typer.Option("BTCUSDT", help="Trading symbol"),
) -> None:
    """Run full multi-timeframe market analysis."""
    _init()
    _run_analysis(symbol)


def _run_analysis(symbol: str) -> None:
    from rich.table import Table
    from rich import box

    from app.data.repository import get_candles
    from app.ta.indicators import add_indicators
    from app.ta.trend import detect_trend_bias
    from app.ta.market_structure import detect_structure, get_range
    from app.ta.liquidity import detect_liquidity_levels
    from app.ta.zones import detect_zones
    from app.ta.zone_scoring import score_zone
    from app.ta.regime import classify_regime

    console.print(f"\n[bold blue]Analyzing {symbol}[/bold blue]\n")

    df_1d  = get_candles(symbol, "1d",  limit=300)
    df_4h  = get_candles(symbol, "4h",  limit=500)
    df_1h  = get_candles(symbol, "1h",  limit=500)
    df_30m = get_candles(symbol, "30m", limit=500)

    for tf, df in [("1d", df_1d), ("4h", df_4h), ("1h", df_1h), ("30m", df_30m)]:
        if df.empty:
            console.print(f"[yellow]??? No {tf} data for {symbol}. Run: backfill[/yellow]")

    # Regime
    regime = classify_regime(df_4h)
    console.print(f"[bold]Market Regime:[/bold] {regime['regime']}")
    for d in regime["details"]:
        console.print(f"  {d}")

    # Trend bias per TF
    t = Table(box=box.SIMPLE_HEAD, title="Trend Bias")
    t.add_column("TF")
    t.add_column("Bias")
    t.add_column("EMA Align")
    t.add_column("Price vs EMA50")
    t.add_column("Score")
    for tf, df in [("Daily", df_1d), ("4H", df_4h), ("1H", df_1h)]:
        if df.empty:
            t.add_row(tf, "???", "???", "???", "???")
            continue
        tb = detect_trend_bias(df)
        color = "green" if "bullish" in tb["bias"] else "red" if "bearish" in tb["bias"] else "yellow"
        t.add_row(
            tf,
            f"[{color}]{tb['bias']}[/{color}]",
            tb["ema_alignment"],
            tb["price_vs_ema50"],
            str(tb["score"]),
        )
    console.print(t)

    # Market structure (4H)
    if not df_4h.empty:
        struct = detect_structure(df_4h)
        console.print(f"\n[bold]4H Market Structure:[/bold] {struct['structure']}")
        if struct.get("last_bos"):
            console.print(f"  BOS: {struct['last_bos']}")
        if struct.get("last_choch"):
            console.print(f"  CHoCH: {struct['last_choch']}")

    # Range / premium-discount
    if not df_30m.empty:
        rng = get_range(df_30m)
        console.print(
            f"\n[bold]30M Range:[/bold]  "
            f"High={rng['range_high']:.2f}  Low={rng['range_low']:.2f}  "
            f"Mid={rng['range_mid']:.2f}  "
            f"Location=[bold]{rng['location']}[/bold]  "
            f"({rng['pct_from_bottom']:.0f}% from bottom)"
        )

    # 30M Zones
    if not df_30m.empty:
        zones = detect_zones(df_30m)
        if zones:
            zt = Table(box=box.SIMPLE_HEAD, title="30M Supply/Demand Zones")
            zt.add_column("Type")
            zt.add_column("Bottom")
            zt.add_column("Top")
            zt.add_column("Body/ATR")
            zt.add_column("Score")
            zt.add_column("Rating")
            for z in zones[:8]:
                sr = score_zone(
                    z, df_30m,
                    h4_bias=detect_trend_bias(df_4h)["bias"] if not df_4h.empty else "neutral",
                    daily_bias=detect_trend_bias(df_1d)["bias"] if not df_1d.empty else "neutral",
                )
                color = "green" if z["zone_type"] == "demand" else "red"
                zt.add_row(
                    f"[{color}]{z['zone_type'].upper()}[/{color}]",
                    f"{z['zone_bottom']:.2f}",
                    f"{z['zone_top']:.2f}",
                    f"{z.get('body_atr_ratio', 0):.2f}",
                    f"{sr['score']:.0f}",
                    sr["rating"],
                )
            console.print(zt)
        else:
            console.print("\n[dim]No supply/demand zones detected on 30M[/dim]")

    # Liquidity
    if not df_30m.empty:
        liq = detect_liquidity_levels(df_30m)
        console.print(f"\n[bold]Liquidity Levels:[/bold] {len(liq)} detected")
        swept = [l for l in liq if l.get("swept")]
        console.print(f"  Recent sweeps: {len(swept)}")


# ?????? signal ????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????

@app.command()
def signal(
    symbol: str = typer.Option("BTCUSDT", help="Trading symbol"),
    capital: float = typer.Option(10_000.0, help="Account capital"),
    save: bool = typer.Option(True, help="Save signal to database"),
) -> None:
    """Generate a BUY / SELL / HOLD signal."""
    _init()
    from app.data.repository import get_candles, save_signal
    from app.ta.signals import generate_signal
    from app.reports.reporter import print_signal_report
    import dataclasses

    df_1d  = get_candles(symbol, "1d",  limit=300)
    df_12h = get_candles(symbol, "12h", limit=200)
    df_4h  = get_candles(symbol, "4h",  limit=500)
    df_1h  = get_candles(symbol, "1h",  limit=500)
    df_30m = get_candles(symbol, "30m", limit=500)
    df_1w  = get_candles(symbol, "1w",  limit=104)

    if df_30m.empty:
        console.print(f"[red]No 30M data for {symbol}. Run backfill first.[/red]")
        sys.exit(1)

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

    print_signal_report(sig)

    if save:
        row = dataclasses.asdict(sig)
        row["liquidity_sweep"] = int(row["liquidity_sweep"])
        save_signal(row)
        console.print("\n[dim]Signal saved to database.[/dim]")


# ?????? backtest ??????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????

@app.command()
def backtest(
    symbol: str = typer.Option("BTCUSDT", help="Trading symbol"),
    start: str = typer.Option("2021-01-01", help="Start date YYYY-MM-DD"),
    end: str = typer.Option("2024-12-31", help="End date YYYY-MM-DD"),
    capital: float = typer.Option(10_000.0, help="Initial capital"),
    risk: float = typer.Option(1.0, help="Risk % per trade"),
) -> None:
    """Run a historical backtest (no lookahead bias)."""
    _init()
    from app.backtesting.engine import run_backtest
    from app.reports.backtest_report import print_detailed_backtest_report
    from app.utils.timeframes import str_to_ms

    start_ms = str_to_ms(start)
    end_ms   = str_to_ms(end)

    console.print(
        f"\n[bold blue]Backtesting {symbol}[/bold blue]  "
        f"{start} ??? {end}  Capital=${capital:,.0f}  Risk={risk}%\n"
    )
    try:
        result = run_backtest(
            symbol=symbol,
            start_ms=start_ms,
            end_ms=end_ms,
            initial_capital=capital,
            risk_pct=risk,
        )
        print_detailed_backtest_report(symbol)
    except ValueError as exc:
        console.print(f"[red]ERROR: {exc}[/red]")
        sys.exit(1)


# ?????? walkforward ??????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????

@app.command()
def walkforward(
    symbol: str = typer.Option("BTCUSDT", help="Trading symbol"),
    start: str = typer.Option("2022-01-01", help="Start date YYYY-MM-DD"),
    end: str = typer.Option("2024-12-31", help="End date YYYY-MM-DD"),
    window_months: int = typer.Option(3, help="Length of each test window in months"),
    capital: float = typer.Option(10_000.0, help="Initial capital per window"),
    risk: float = typer.Option(1.0, help="Risk % per trade"),
) -> None:
    """
    Walk-forward validation: run independent backtests on sequential windows.

    Shows if performance is consistent across time or only works in specific
    market conditions. High variance between windows = likely curve-fitted.
    """
    _init()
    from rich.table import Table
    from rich import box
    from app.backtesting.walk_forward import run_walk_forward
    from app.utils.timeframes import str_to_ms

    start_ms = str_to_ms(start)
    end_ms   = str_to_ms(end)

    console.print(
        f"\n[bold blue]Walk-Forward Validation: {symbol}[/bold blue]  "
        f"{start} ??? {end}  Window={window_months}mo  Capital=${capital:,.0f}\n"
    )

    try:
        result = run_walk_forward(
            symbol=symbol,
            start_ms=start_ms,
            end_ms=end_ms,
            window_months=window_months,
            initial_capital=capital,
            risk_pct=risk,
        )
    except ValueError as exc:
        console.print(f"[red]ERROR: {exc}[/red]")
        sys.exit(1)

    windows = result["windows"]
    summary = result["summary"]

    # Per-window table
    t = Table(box=box.SIMPLE_HEAD, title=f"Walk-Forward Windows ({symbol})")
    t.add_column("Period",       style="bold")
    t.add_column("Trades",       justify="right")
    t.add_column("Win Rate",     justify="right")
    t.add_column("Profit Factor",justify="right")
    t.add_column("Max DD %",     justify="right")
    t.add_column("Net Profit %", justify="right")

    for w in windows:
        if w["error"]:
            t.add_row(w["period"], "???", "???", "???", "???", f"[red]ERR: {w['error'][:40]}[/red]")
            continue
        wr = w["win_rate"]
        pf = w["profit_factor"]
        np_ = w["net_profit_pct"]
        wr_color  = "green" if wr >= 0.5 else "red"
        pf_color  = "green" if pf >= 1.0 else "red"
        np_color  = "green" if np_ >= 0 else "red"
        t.add_row(
            w["period"],
            str(w["total_trades"]),
            f"[{wr_color}]{wr*100:.1f}%[/{wr_color}]",
            f"[{pf_color}]{pf:.2f}[/{pf_color}]",
            f"{w['max_drawdown_pct']:.1f}%",
            f"[{np_color}]{np_:+.1f}%[/{np_color}]",
        )

    console.print(t)

    # Summary
    if summary.get("valid_windows", 0) > 0:
        console.print(f"\n[bold]Summary ({summary['valid_windows']}/{summary['total_windows']} valid windows)[/bold]")
        console.print(f"  Win rate:          {summary['win_rate_mean']*100:.1f}% ?? {summary['win_rate_std']*100:.1f}%")
        console.print(f"  Profitable windows:{summary['profitable_windows_pct']:.0f}%")
        console.print(f"  Profit factor:     best={summary['best_profit_factor']:.2f}  worst={summary['worst_profit_factor']:.2f}")
        console.print(f"  Worst drawdown:    {summary['worst_drawdown']:.1f}%")
        console.print(f"  Total trades:      {summary['total_trades']}")

        consistency = summary["win_rate_std"] * 100
        if consistency < 5:
            console.print("\n[green]??? Consistent ??? low variance across windows[/green]")
        elif consistency < 12:
            console.print("\n[yellow]??? Moderate variance ??? review regime sensitivity[/yellow]")
        else:
            console.print("\n[red]??? High variance ??? strategy may be regime-dependent or curve-fitted[/red]")
    else:
        console.print("[yellow]No windows produced tradeable signals.[/yellow]")


# ?????? paper ???????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????

@app.command()
def paper(
    symbol: str = typer.Option("BTCUSDT", help="Trading symbol"),
    capital: Optional[float] = typer.Option(None, help="Starting capital (first run only)"),
    risk: float = typer.Option(1.0, help="Risk % per trade"),
) -> None:
    """Run one paper trading cycle."""
    _init()
    from app.data.repository import get_candles, save_signal
    from app.paper.account import get_paper_capital, set_paper_capital
    from app.paper.trade_manager import run_paper_session
    from app.reports.reporter import print_signal_report
    import dataclasses

    # Set paper capital only when explicitly provided
    if capital is not None:
        set_paper_capital(symbol, capital)
    current_capital = get_paper_capital(symbol)

    df_1d  = get_candles(symbol, "1d",  limit=300)
    df_12h = get_candles(symbol, "12h", limit=200)
    df_4h  = get_candles(symbol, "4h",  limit=500)
    df_1h  = get_candles(symbol, "1h",  limit=500)
    df_30m = get_candles(symbol, "30m", limit=500)
    df_1w  = get_candles(symbol, "1w",  limit=104)

    if df_30m.empty:
        console.print(f"[red]No 30M data. Run backfill first.[/red]")
        sys.exit(1)

    result = run_paper_session(
        symbol=symbol,
        df_1d=df_1d,
        df_12h=df_12h,
        df_4h=df_4h,
        df_1h=df_1h,
        df_30m=df_30m,
        df_1w=df_1w,
        capital=current_capital,
        risk_pct=risk,
    )

    print_signal_report(result["signal"])

    console.print(f"\n[bold]Paper Account[/bold]")
    console.print(f"  Capital: ${result['capital']:,.2f}")
    if result["trade_opened"]:
        console.print(f"  [green]Trade opened: #{result['trade_opened']}[/green]")
    if result.get("pending_order"):
        console.print(f"  [cyan]Limit order placed: #{result['pending_order']}[/cyan]")
    if result["closed_trades"]:
        for t in result["closed_trades"]:
            pnl = t.get("pnl", 0)
            color = "green" if pnl >= 0 else "red"
            console.print(f"  [{color}]Closed {t['direction']}: PnL=${pnl:,.2f}[/{color}]")

    sm = result["summary"]
    if sm and sm.get("total"):
        wr = (sm["wins"] or 0) / sm["total"] * 100
        console.print(
            f"  Paper history: {sm['total']} trades  "
            f"WR={wr:.1f}%  Total PnL=${sm.get('total_pnl', 0):,.2f}"
        )


# ?????? close-all ???????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????

@app.command(name="close-all")
def close_all(
    dry_run: bool = typer.Option(False, "--dry-run", help="Print what would be closed without writing"),
) -> None:
    """Close all open paper trades at current market price."""
    _init()
    import time as _time
    from app.db.connection import get_conn
    from app.data.repository import close_paper_trade
    from app.data.binance_client import BinanceClient
    from app.paper.account import get_paper_capital, update_capital_after_trade
    from app.config import get_settings
    import sqlite3

    cfg = get_settings()
    fee_rate = cfg.backtest_fee_pct / 100
    client = BinanceClient()

    with get_conn() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, symbol, direction, entry_price, position_size, partial_pnl "
            "FROM paper_trades WHERE status='open'"
        ).fetchall()

    if not rows:
        console.print("[yellow]No open trades.[/yellow]")
        return

    now_ms = int(_time.time() * 1000)
    total_pnl = 0.0

    for row in rows:
        tid      = row["id"]
        sym      = row["symbol"]
        direction = row["direction"]
        entry    = float(row["entry_price"])
        pos_size = float(row["position_size"])

        cp = client.get_ticker_price(sym)
        if cp is None:
            console.print(f"  [red]SKIP #{tid} {sym}: could not fetch price[/red]")
            continue
        cp = float(cp)

        raw_pnl = (entry - cp) * pos_size if direction == "SELL" else (cp - entry) * pos_size
        fee     = entry * pos_size * fee_rate * 2
        pnl_increment = round(raw_pnl - fee, 2)
        # Total trade PnL includes any partial TP already realized; only the
        # increment goes to capital (the partial was applied when taken).
        pnl     = round(pnl_increment + float(row["partial_pnl"] or 0), 2)
        capital = get_paper_capital(sym)
        pnl_pct = round(pnl / capital * 100, 4) if capital > 0 else 0.0
        total_pnl += pnl

        color = "green" if pnl >= 0 else "red"
        prefix = "Would close" if dry_run else "Closed"
        console.print(
            f"  [{color}]{prefix} #{tid} {sym} {direction}  "
            f"entry={entry:.4f}  close={cp:.4f}  "
            f"pnl=${pnl:+.2f} ({pnl_pct:+.3f}%)[/{color}]"
        )

        if not dry_run:
            close_paper_trade(tid, cp, now_ms, pnl, pnl_pct, "closed")
            update_capital_after_trade(sym, pnl_increment)

    color = "green" if total_pnl >= 0 else "red"
    label = "Would realise" if dry_run else "Total realised"
    console.print(f"\n  [{color}]{label}: ${total_pnl:+.2f}[/{color}]")


# ?????? train ???????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????

@app.command()
def train(
    symbol: str = typer.Option("BTCUSDT", help="Trading symbol"),
    model: str = typer.Option("both", help="Model to train: buy | sell | both"),
    source: str = typer.Option(
        "snapshots",
        help="Training data source: snapshots (trade outcomes) | candles (independent labels) | both",
    ),
) -> None:
    """Train BUY and/or SELL classification models."""
    _init()
    from app.models.trainer import train_model, train_from_candles

    targets = ["buy", "sell"] if model == "both" else [model]
    sources = ["snapshots", "candles"] if source == "both" else [source]

    for mt in targets:
        for src in sources:
            label = f"{mt.upper()} ({src})"
            console.print(f"\n[bold blue]Training {label} model for {symbol}...[/bold blue]")
            try:
                if src == "candles":
                    metrics = train_from_candles(symbol=symbol, model_type=mt)
                else:
                    metrics = train_model(symbol=symbol, model_type=mt)
                console.print(f"[green]??? {label} model trained[/green]")
                console.print(f"  Version:     {metrics['version']}")
                console.print(f"  Samples:     {metrics['samples']} ({metrics['wins']}W / {metrics['losses']}L)")
                console.print(f"  Win rate:    {metrics['win_rate']*100:.1f}%")
                console.print(f"  CV F1:       {metrics['cv_f1_mean']:.4f} ?? {metrics['cv_f1_std']:.4f}")
                console.print(f"  Train acc.:  {metrics['train_accuracy']*100:.1f}%")
                console.print(f"\n  [dim]Run validate to accept this model:[/dim]")
                console.print(f"  [dim]python main.py validate --symbol {symbol} --model-version {metrics['version']}[/dim]")
            except (ValueError, ImportError) as exc:
                console.print(f"[red]Cannot train {label}: {exc}[/red]")


# ?????? validate ??????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????

@app.command()
def validate(
    symbol: str = typer.Option("BTCUSDT", help="Trading symbol"),
    model_version: str = typer.Option("latest", help="Model version to validate"),
    model_type: str = typer.Option("both", help="buy | sell | both"),
    accept: bool = typer.Option(False, "--accept", help="Automatically activate if validation passes"),
) -> None:
    """Validate a candidate model against out-of-sample data."""
    _init()
    from app.models.validator import validate_model, activate_model
    from app.models.versioning import model_file_path
    from app.data.repository import get_conn
    from pathlib import Path

    targets = ["buy", "sell"] if model_type == "both" else [model_type]

    for mt in targets:
        console.print(f"\n[bold blue]Validating {mt.upper()} model for {symbol}...[/bold blue]")

        # Find candidate model file
        # When model_type="both" with an explicit version, the version refers to
        # that specific model type only ??? fall back to "latest" for the other.
        use_version = model_version if model_type != "both" else "latest"
        if use_version == "latest":
            with get_conn() as conn:
                row = conn.execute(
                    "SELECT version, file_path FROM model_versions "
                    "WHERE symbol=? AND model_type=? AND status='candidate' "
                    "ORDER BY created_at DESC LIMIT 1",
                    (symbol, mt),
                ).fetchone()
            if not row:
                console.print(f"[yellow]No candidate {mt} model found. Train one first.[/yellow]")
                continue
            version   = row[0]
            file_path = Path(row[1])
        else:
            version   = use_version
            file_path = model_file_path(symbol, mt, version)

        if not file_path.exists():
            console.print(f"[red]Model file not found: {file_path}[/red]")
            continue

        report = validate_model(symbol=symbol, model_type=mt, candidate_path=file_path)
        accepted = report.get("accepted", False)

        color = "green" if accepted else "red"
        console.print(f"  Result: [{color}]{'ACCEPTED' if accepted else 'REJECTED'}[/{color}]")
        console.print(f"  Reason: {report.get('reason', '???')}")
        if report.get("candidate_f1"):
            console.print(f"  Candidate F1:   {report['candidate_f1']:.4f}")
        if report.get("current_f1") is not None:
            console.print(f"  Current F1:     {report['current_f1']:.4f}")
            if report.get("f1_improvement") is not None:
                imp = report["f1_improvement"]
                console.print(f"  Improvement:    {imp:+.4f}")

        if accepted and accept:
            activate_model(symbol, mt, version)
            console.print(f"  [green]??? Model {version} activated as {mt} model.[/green]")
        elif accepted and not accept:
            console.print(
                f"\n  [dim]To activate: python main.py validate --symbol {symbol} "
                f"--model-version {version} --model-type {mt} --accept[/dim]"
            )


# ?????? report ????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????

@app.command()
def report(
    symbol: str = typer.Option("BTCUSDT", help="Trading symbol"),
    type_: str = typer.Option("general", "--type", help="Report type: general | backtest | learning"),
) -> None:
    """Generate a performance / learning report."""
    _init()
    from app.reports.reporter import print_general_report, print_backtest_report
    from app.reports.backtest_report import print_detailed_backtest_report
    from app.reports.learning_report import print_learning_report

    if type_ == "backtest":
        print_detailed_backtest_report(symbol)
    elif type_ == "learning":
        print_learning_report(symbol)
    else:
        print_general_report(symbol)


# ?????? analyze-trades ????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????

@app.command(name="analyze-trades")
def analyze_trades() -> None:
    """
    Full paper-trade analysis: performance, breakdowns by symbol/direction/
    setup/regime/hour, MFE/MAE entry diagnostics, capital reconciliation
    and data-hygiene checks.
    """
    _init()
    from app.reports.trade_analysis import print_trade_analysis
    print_trade_analysis()


# ?????? sync-universe ???????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????

@app.command(name="sync-universe")
def sync_universe_cmd(
    top: int = typer.Option(50, "--top", "-n", help="How many top-volume coins to consider"),
    quote: str = typer.Option("USDT", help="Quote currency filter (default USDT)"),
    start: str = typer.Option("2021-01-01", help="Backfill start date for new coins"),
    min_volume: float = typer.Option(50_000_000, "--min-volume", help="Min 24h quote volume in USD"),
    min_age: int = typer.Option(90, "--min-age", help="Min listing age in days (skip newer coins)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be backfilled without doing it"),
    backtest_pool: bool = typer.Option(
        True, "--backtest-pool/--no-backtest-pool",
        help="After sync, backtest unvalidated coins in coin_pool.json",
    ),
) -> None:
    """
    Discover and backfill new top coins from Binance.

    Fetches the top-N USDT pairs ranked by 24h volume, compares against your
    local DB, and backfills any coins not yet stored ??? all 6 timeframes
    (30m, 1h, 4h, 12h, 1d, 1w) from the given start date.

    Examples:
        python main.py sync-universe
        python main.py sync-universe --top 100 --min-volume 20000000
        python main.py sync-universe --dry-run   # preview only
    """
    _init()
    from app.data.universe import sync_universe
    sync_universe(
        n=top,
        quote=quote,
        start=start,
        min_volume_usd=min_volume,
        min_listing_age_days=min_age,
        dry_run=dry_run,
    )

    if backtest_pool and not dry_run:
        pool = _load_coin_pool()
        if pool:
            from scan_universe import auto_backtest_new_symbols
            console.print("\n[bold]Backtesting unvalidated pool coins...[/bold]")
            validated = auto_backtest_new_symbols(months=12, pool=pool)
            if validated:
                console.print(f"[green]Newly validated: {validated}[/green]")
            else:
                console.print("[dim]No new validations this run.[/dim]")


# ?????? live ??????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????

@app.command()
def live(
    symbols: List[str] = typer.Option(["BTCUSDT"], "--symbol", "-s", help="Symbol(s) to watch. Ignored when --auto is set."),
    timeframe: str = typer.Option("30m", help="Execution timeframe (candle close interval)"),
    capital: Optional[float] = typer.Option(None, help="Paper capital applied to each symbol on first run"),
    risk: float = typer.Option(1.0, help="Risk % per trade per symbol"),
    heartbeat: int = typer.Option(8, help="Send Telegram heartbeat every N cycles (0=off)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Generate signals but do NOT open trades"),
    auto: bool = typer.Option(False, "--auto", help="Auto-select top symbols from universe scanner at startup"),
    auto_n: int = typer.Option(0, "--auto-n", help="Symbols to auto-select (0 = all validated, default)"),
    auto_validated: bool = typer.Option(True, "--auto-validated/--auto-all", help="Auto-select from validated symbols only (default: validated only)"),
    sync: bool = typer.Option(False, "--sync", help="Sync universe from Binance top-50 before scanning (backfills new coins automatically)"),
    sync_top: int = typer.Option(50, "--sync-top", help="How many top-volume coins to consider when syncing"),
    sync_min_volume: float = typer.Option(50_000_000, "--sync-min-volume", help="Min 24h volume (USD) for sync"),
    rescan_every: int = typer.Option(144, "--rescan-every", help="Re-run universe scanner every N cycles to rotate coins mid-session (0=off, 144=every 3 days on 30m tf). Symbols with open trades are pinned until the trade closes."),
    rescan_n: int = typer.Option(0, "--rescan-n", help="Symbols to select on each rescan (0 = all validated, default)"),
    rescan_validated: bool = typer.Option(True, "--rescan-validated/--rescan-all", help="Rescan from validated symbols only (default: validated only)"),
    live_execution: bool = typer.Option(False, "--live-execution", help="Execute real orders on Binance Futures (requires BINANCE_API_KEY/SECRET in .env). Does NOT satisfy Owner/Risk live-entry gates."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt (for nohup/background use). Does NOT satisfy Owner/Risk live-entry gates."),
) -> None:
    """
    Run the live auto-signal watcher (single or multi-symbol).

    Manual symbol selection:
        python main.py live --symbol BTCUSDT
        python main.py live --symbol BTCUSDT --symbol ETHUSDT --symbol SOLUSDT --symbol BNBUSDT

    Auto-selection from universe scanner:
        python main.py live --auto
        python main.py live --auto --auto-n 6
        python main.py live --auto --auto-all    # include unvalidated symbols too

    Auto-select AND sync universe from Binance top-50 first:
        python main.py live --auto --sync
        python main.py live --auto --sync --sync-top 100

    Portfolio guards apply across all symbols:
      - Max 3 open trades total (portfolio cap)
      - Max 2 trades in the same direction (correlation dampener)

    Sleeps until each candle close, then runs the full pipeline for every
    symbol in sequence. Press Ctrl+C to stop gracefully.
    """
    _init()
    import sys; sys.path.insert(0, ".")

    coin_pool = _load_coin_pool()

    # ?????? Live execution branch ???????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????
    if live_execution:
        from app.live.live_entry_auth import evaluate_live_entry_from_env
        from app.live.live_watcher import LiveTradingWatcher
        from app.config import get_settings as _cfg

        # Fail-closed Owner/Risk/LIVE_HALT gates. --yes and --live-execution
        # never satisfy them. Paper path (the else branch below) is unchanged.
        if not dry_run:
            _auth = evaluate_live_entry_from_env(
                yes_flag=yes, live_execution=True,
            )
            if not _auth.allowed:
                console.print(
                    f"[bold red]ERROR[/bold red] Live entry denied "
                    f"({_auth.deny_code}): {_auth.reason}"
                )
                raise typer.Exit(1)

        cfg = _cfg()
        if not cfg.binance_api_key or not cfg.binance_api_secret:
            console.print(
                "[bold red]ERROR[/bold red] BINANCE_API_KEY and BINANCE_API_SECRET "
                "must be set in .env before using --live-execution"
            )
            raise typer.Exit(1)

        # Fetch balance for the confirmation prompt
        from app.live.exchange import BinanceExchangeClient
        _client = BinanceExchangeClient(
            api_key    = cfg.binance_api_key,
            api_secret = cfg.binance_api_secret,
            testnet    = cfg.binance_testnet,
        )
        try:
            _balance = _client.get_balance()
        except Exception as exc:
            console.print(f"[bold red]ERROR[/bold red] Could not connect to Binance: {exc}")
            raise typer.Exit(1)

        net_label = "TESTNET (safe)" if cfg.binance_testnet else "[bold red]MAINNET ??? REAL MONEY[/bold red]"
        console.print()
        console.print(f"[bold red]???  LIVE EXECUTION MODE[/bold red]")
        console.print(f"   Network:  {net_label}")
        console.print(f"   Balance:  ${_balance:,.2f} USDT")
        console.print(f"   Risk:     {cfg.live_risk_pct}% per trade")
        console.print(f"   Max lev:  {cfg.live_max_leverage}??")
        console.print()

        if not dry_run and not yes:
            confirm = typer.confirm("Real orders will be placed on Binance Futures. Continue?")
            if not confirm:
                console.print("[dim]Aborted.[/dim]")
                raise typer.Exit(0)

        if auto:
            from scan_universe import select_watchlist
            selected = select_watchlist(
                n=auto_n,
                validated_only=auto_validated,
                fallback_symbols=list(symbols),
                verbose=True,
                pool=coin_pool,
            )
        else:
            selected = list(symbols)

        live_watcher = LiveTradingWatcher(
            symbols          = selected,
            exec_timeframe   = timeframe,
            heartbeat_every  = heartbeat,
            dry_run          = dry_run,
            rescan_every     = rescan_every,
            rescan_n         = rescan_n,
            rescan_validated = rescan_validated,
            rescan_pool      = coin_pool,
            yes_flag         = yes,
        )
        live_watcher.run()
        return

    # ?????? Paper watcher branch (default) ?????????????????????????????????????????????????????????????????????????????????????????????????????????????????????
    from app.live.watcher import LiveWatcher

    # Optional: sync universe from Binance, then auto-backtest any new coins
    # Backtests run in a background thread so the watcher starts immediately
    # and doesn't miss candle closes. New symbols appear in the watchlist on
    # the next rescan (cycle 144) once their backtests complete.
    if sync:
        import threading as _t
        from app.data.universe import sync_universe
        from scan_universe import auto_backtest_new_symbols
        sync_universe(n=sync_top, min_volume_usd=sync_min_volume)
        _bt_thread = _t.Thread(
            target=auto_backtest_new_symbols,
            name="AutoBacktest",
            daemon=True,
        )
        _bt_thread.start()

    if auto:
        from scan_universe import select_watchlist
        selected = select_watchlist(
            n=auto_n,
            validated_only=auto_validated,
            fallback_symbols=list(symbols),
            verbose=True,
            pool=coin_pool,
        )
    else:
        selected = list(symbols)

    watcher = LiveWatcher(
        symbols=selected,
        exec_timeframe=timeframe,
        heartbeat_every=heartbeat,
        paper_capital=capital,
        risk_pct=risk,
        dry_run=dry_run,
        rescan_every=rescan_every,
        rescan_n=rescan_n,
        rescan_validated=rescan_validated,
        sync=sync,
        sync_top=sync_top,
        sync_min_volume=sync_min_volume,
        rescan_pool=coin_pool,
    )
    watcher.run()

