#!/usr/bin/env python3
"""
LQ-MTF Strategy — Interactive Menu
Run: python menu.py
"""

import sys
import io
import os

# Force UTF-8 on Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv()

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt, Confirm, FloatPrompt, IntPrompt  # type: ignore[attr-defined]
from rich.text import Text
from rich import box
from rich.align import Align
from rich.columns import Columns
from rich.rule import Rule

from app.config import get_settings

console = Console()

# ── Colour palette ────────────────────────────────────────────────────────────
TITLE_COLOR  = "bold cyan"
MENU_COLOR   = "bold white"
HINT_COLOR   = "dim"
OK_COLOR     = "bold green"
WARN_COLOR   = "bold yellow"
ERR_COLOR    = "bold red"
ACCENT_COLOR = "bold magenta"

# ── State (persists across menu loops) ───────────────────────────────────────
STATE = {
    "symbol":  "BTCUSDT",
    "capital": 10_000.0,
    "risk":    1.0,
}


# ── Header ────────────────────────────────────────────────────────────────────

def print_header() -> None:
    console.clear()
    banner = Text(justify="center")
    banner.append("\n  LQ-MTF Strategy  \n", style="bold cyan on dark_blue")
    banner.append("  Liquidity-Quality Multi-Timeframe System  \n", style="dim cyan")

    console.print(Panel(
        Align.center(banner),
        border_style="cyan",
        padding=(0, 4),
    ))
    console.print(
        f"  Symbol: [bold yellow]{STATE['symbol']}[/bold yellow]   "
        f"Capital: [bold green]${STATE['capital']:,.0f}[/bold green]   "
        f"Risk: [bold]{STATE['risk']}%[/bold] per trade\n"
    )


# ── Main menu ─────────────────────────────────────────────────────────────────

MAIN_MENU = [
    ("1", "Backfill Historical Data",  "Download OHLCV candles from Binance",           "cyan"),
    ("2", "Update Latest Data",        "Fetch only new/missing candles",                "cyan"),
    ("3", "Analyze Market",            "Full multi-timeframe analysis",                 "blue"),
    ("4", "Generate Signal",           "BUY / SELL / HOLD with confluence",             "green"),
    ("5", "Run Backtest",              "Historical simulation (no lookahead)",          "yellow"),
    ("6", "Paper Trading",             "One paper trade cycle",                         "magenta"),
    ("7", "Train Models",              "Train BUY / SELL classifiers",                  "magenta"),
    ("8", "Validate & Activate Model", "Out-of-sample check, activate if pass",        "magenta"),
    ("9", "Reports",                   "Performance, backtest, learning reports",       "blue"),
    ("L", "Live Mode",                 "Auto-signal watcher + Telegram alerts [LIVE]", "bold green"),
    ("S", "Settings",                  "Change symbol, capital, risk",                  "dim"),
    ("0", "Exit",                      "",                                               "red"),
]


def print_main_menu() -> None:
    t = Table(box=box.SIMPLE, show_header=False, padding=(0, 2), expand=False)
    t.add_column("Key",   style="bold yellow", width=4,  no_wrap=True)
    t.add_column("Action", style=MENU_COLOR,   width=30, no_wrap=True)
    t.add_column("Hint",   style=HINT_COLOR,   width=42)

    for key, action, hint, _ in MAIN_MENU:
        style = "bold red" if key == "0" else ""
        t.add_row(f"[{key}]", action, hint, style=style)

    console.print(Align.center(t))
    console.print()


def main_menu() -> None:
    """Main interactive loop."""
    # Ensure DB is ready
    from app.db.migrations import run_migrations
    run_migrations()

    while True:
        print_header()
        print_main_menu()

        choice = Prompt.ask(
            "  [bold yellow]Choose an option[/bold yellow]",
            choices=[m[0] for m in MAIN_MENU],
            show_choices=False,
        ).upper()

        if choice == "0":
            console.print("\n[dim]Goodbye.[/dim]\n")
            break
        elif choice == "1":
            menu_backfill()
        elif choice == "2":
            menu_update()
        elif choice == "3":
            menu_analyze()
        elif choice == "4":
            menu_signal()
        elif choice == "5":
            menu_backtest()
        elif choice == "6":
            menu_paper()
        elif choice == "7":
            menu_train()
        elif choice == "8":
            menu_validate()
        elif choice == "9":
            menu_reports()
        elif choice == "L":
            menu_live()
        elif choice == "S":
            menu_settings()

        _pause()


# ── 1. Backfill ───────────────────────────────────────────────────────────────

def menu_backfill() -> None:
    _section_header("Backfill Historical Data")

    symbol = _ask_symbol()
    start  = Prompt.ask("  Start date", default="2021-01-01")
    end    = Prompt.ask("  End date (leave blank = today)", default="")

    tf_choices = {
        "1": ["1d", "4h", "1h", "30m"],
        "2": ["1d", "4h", "1h", "30m", "15m"],
        "3": ["1d"],
        "4": ["4h"],
        "5": ["1h"],
        "6": ["30m"],
    }
    console.print("\n  Timeframe presets:")
    console.print("    [1] Standard  — 1d, 4h, 1h, 30m  [bold](recommended)[/bold]")
    console.print("    [2] Extended  — 1d, 4h, 1h, 30m, 15m")
    console.print("    [3] Daily only")
    console.print("    [4] 4H only")
    console.print("    [5] 1H only")
    console.print("    [6] 30M only")
    tf_key = Prompt.ask("  Select preset", choices=list(tf_choices), default="1")
    timeframes = tf_choices[tf_key]

    resume = Confirm.ask("  Resume interrupted download?", default=True)

    console.print()
    if not Confirm.ask(
        f"  Download [bold]{symbol}[/bold] {timeframes} from {start}?",
        default=True,
    ):
        return

    from app.data.backfill import backfill as _backfill
    from app.utils.timeframes import str_to_ms
    import time as _time

    start_ms = str_to_ms(start)
    end_ms   = str_to_ms(end) if end else int(_time.time() * 1000)

    console.print()
    try:
        _backfill(symbol=symbol, timeframes=timeframes,
                  start_ms=start_ms, end_ms=end_ms, resume=resume)
        console.print(f"\n  [{OK_COLOR}]Backfill complete.[/{OK_COLOR}]")
    except Exception as exc:
        console.print(f"\n  [{ERR_COLOR}]ERROR: {exc}[/{ERR_COLOR}]")


# ── 2. Update ─────────────────────────────────────────────────────────────────

def menu_update() -> None:
    _section_header("Update Latest Data")

    symbol = _ask_symbol()
    timeframes = _ask_timeframes()

    console.print()
    from app.data.backfill import update_latest
    try:
        counts = update_latest(symbol=symbol, timeframes=timeframes)
        total = sum(counts.values())
        console.print(f"\n  [{OK_COLOR}]Done — {total} new candles inserted.[/{OK_COLOR}]")
    except Exception as exc:
        console.print(f"\n  [{ERR_COLOR}]ERROR: {exc}[/{ERR_COLOR}]")


# ── 3. Analyze ────────────────────────────────────────────────────────────────

def menu_analyze() -> None:
    _section_header("Market Analysis")

    symbol = _ask_symbol()
    console.print()

    from app.data.repository import get_candles
    from app.ta.indicators import add_indicators
    from app.ta.trend import detect_trend_bias
    from app.ta.market_structure import detect_structure, get_range
    from app.ta.liquidity import detect_liquidity_levels, detect_sweeps
    from app.ta.zones import detect_zones
    from app.ta.zone_scoring import score_zone
    from app.ta.regime import classify_regime

    df_1d  = get_candles(symbol, "1d",  limit=300)
    df_4h  = get_candles(symbol, "4h",  limit=500)
    df_1h  = get_candles(symbol, "1h",  limit=500)
    df_30m = get_candles(symbol, "30m", limit=500)

    if df_30m.empty:
        console.print(f"  [{WARN_COLOR}]No 30M data. Run Backfill first.[/{WARN_COLOR}]")
        return

    # ── Regime ───────────────────────────────────────────────────────────────
    regime = classify_regime(df_4h) if not df_4h.empty else {"regime": "unknown", "details": []}
    regime_color = _regime_color(regime["regime"])
    console.print(Panel(
        f"Market Regime: [{regime_color}]{regime['regime'].replace('_', ' ').upper()}[/{regime_color}]",
        border_style=regime_color,
        padding=(0, 2),
    ))
    for d in regime.get("details", []):
        console.print(f"    {d}")

    # ── Trend bias table ──────────────────────────────────────────────────────
    console.print()
    t = Table(title="Trend Bias", box=box.SIMPLE_HEAD)
    t.add_column("TF",        width=8)
    t.add_column("Bias",      width=20)
    t.add_column("EMA",       width=14)
    t.add_column("vs EMA50",  width=10)
    t.add_column("Score",     width=7)

    for label, df in [("Daily", df_1d), ("4H", df_4h), ("1H", df_1h)]:
        if df.empty:
            t.add_row(label, "[dim]No data[/dim]", "—", "—", "—")
            continue
        tb = detect_trend_bias(df)
        bias = tb["bias"]
        c = "green" if "bullish" in bias else "red" if "bearish" in bias else "yellow"
        t.add_row(
            label,
            f"[{c}]{bias}[/{c}]",
            tb["ema_alignment"],
            tb["price_vs_ema50"],
            f"{tb['score']:+d}",
        )
    console.print(t)

    # ── 4H structure ─────────────────────────────────────────────────────────
    if not df_4h.empty:
        s = detect_structure(df_4h)
        sc = "green" if s["structure"] == "bullish" else "red" if s["structure"] == "bearish" else "yellow"
        console.print(f"\n  4H Structure: [{sc}]{s['structure'].upper()}[/{sc}]", end="")
        if s.get("last_bos"):
            bos_c = "green" if s["last_bos"] == "bullish" else "red"
            console.print(f"  |  BOS: [{bos_c}]{s['last_bos']}[/{bos_c}]", end="")
        if s.get("last_choch"):
            choc = "green" if s["last_choch"] == "bullish" else "red"
            console.print(f"  |  CHoCH: [{choc}]{s['last_choch']}[/{choc}]", end="")
        console.print()

    # ── Range / premium-discount ──────────────────────────────────────────────
    rng = get_range(df_30m)
    loc = rng["location"]
    loc_c = "green" if "discount" in loc else "red" if "premium" in loc else "yellow"
    console.print(
        f"\n  30M Range  High=[bold]{rng['range_high']:.2f}[/bold]"
        f"  Low=[bold]{rng['range_low']:.2f}[/bold]"
        f"  Mid={rng['range_mid']:.2f}"
        f"  Location=[{loc_c}]{loc.upper()}[/{loc_c}]"
        f"  ({rng['pct_from_bottom']:.0f}% from bottom)"
    )

    # ── Zones ─────────────────────────────────────────────────────────────────
    zones = detect_zones(df_30m)
    h4_bias = detect_trend_bias(df_4h)["bias"] if not df_4h.empty else "neutral"
    d1_bias = detect_trend_bias(df_1d)["bias"] if not df_1d.empty else "neutral"

    if zones:
        console.print()
        zt = Table(title=f"30M Supply/Demand Zones ({len(zones)} found)", box=box.SIMPLE_HEAD)
        zt.add_column("Type",    width=8)
        zt.add_column("Bottom",  width=12, justify="right")
        zt.add_column("Top",     width=12, justify="right")
        zt.add_column("Body/ATR",width=9,  justify="right")
        zt.add_column("Score",   width=7,  justify="right")
        zt.add_column("Rating",  width=8)
        zt.add_column("Touch#",  width=7,  justify="right")

        for z in zones[:10]:
            sr = score_zone(z, df_30m, h4_bias, d1_bias)
            c = "green" if z["zone_type"] == "demand" else "red"
            rating_c = "green" if sr["rating"] in ("A+", "A") else "yellow" if sr["rating"] == "B" else "dim"
            zt.add_row(
                f"[{c}]{z['zone_type'][:6].upper()}[/{c}]",
                f"{z['zone_bottom']:.2f}",
                f"{z['zone_top']:.2f}",
                f"{z.get('body_atr_ratio', 0):.2f}",
                f"{sr['score']:.0f}",
                f"[{rating_c}]{sr['rating']}[/{rating_c}]",
                str(z.get("touch_count", 0)),
            )
        console.print(zt)
    else:
        console.print("\n  [dim]No supply/demand zones detected on 30M[/dim]")

    # ── Liquidity ─────────────────────────────────────────────────────────────
    liq = detect_liquidity_levels(df_30m)
    liq = detect_sweeps(df_30m, liq)
    swept = [l for l in liq if l.get("swept")]
    console.print(f"\n  Liquidity levels: {len(liq)} detected  |  Recent sweeps: [bold]{len(swept)}[/bold]")
    if swept:
        for l in swept[-3:]:
            console.print(f"    [yellow]Swept[/yellow] {l['level_type']} @ {l['price']:.4f}")


# ── 4. Signal ─────────────────────────────────────────────────────────────────

def menu_signal() -> None:
    _section_header("Generate Signal")

    symbol  = _ask_symbol()
    capital = _ask_capital()
    save    = Confirm.ask("  Save signal to database?", default=True)
    console.print()

    from app.data.repository import get_candles, save_signal as _save_signal
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
        console.print(f"  [{WARN_COLOR}]No 30M data. Run Backfill first.[/{WARN_COLOR}]")
        return

    with console.status("[bold cyan]Generating signal...[/bold cyan]"):
        sig = generate_signal(
            symbol=symbol, df_1d=df_1d, df_4h=df_4h,
            df_1h=df_1h, df_30m=df_30m, capital=capital,
            df_12h=df_12h, df_1w=df_1w,
        )

    print_signal_report(sig)

    if save:
        row = dataclasses.asdict(sig)
        row["liquidity_sweep"] = int(row["liquidity_sweep"])
        _save_signal(row)
        console.print("  [dim]Signal saved.[/dim]")


# ── 5. Backtest ───────────────────────────────────────────────────────────────

def menu_backtest() -> None:
    _section_header("Run Backtest")

    symbol  = _ask_symbol()
    start   = Prompt.ask("  Start date", default="2021-01-01")
    end     = Prompt.ask("  End date",   default="2024-12-31")
    capital = _ask_capital()
    risk    = _ask_risk()

    console.print()
    if not Confirm.ask(
        f"  Backtest [bold]{symbol}[/bold] {start}–{end}  Capital=${capital:,.0f}  Risk={risk}%?",
        default=True,
    ):
        return

    from app.backtesting.engine import run_backtest
    from app.reports.backtest_report import print_detailed_backtest_report
    from app.utils.timeframes import str_to_ms

    console.print()
    try:
        run_backtest(
            symbol=symbol,
            start_ms=str_to_ms(start),
            end_ms=str_to_ms(end),
            initial_capital=capital,
            risk_pct=risk,
        )
        console.print()
        print_detailed_backtest_report(symbol)
    except ValueError as exc:
        console.print(f"  [{ERR_COLOR}]ERROR: {exc}[/{ERR_COLOR}]")


# ── 6. Paper trading ──────────────────────────────────────────────────────────

def menu_paper() -> None:
    _section_header("Paper Trading")

    symbol  = _ask_symbol()
    capital = _ask_capital()
    risk    = _ask_risk()
    console.print()

    from app.data.repository import get_candles, save_signal as _save_signal
    from app.paper.account import get_paper_capital, set_paper_capital
    from app.paper.trade_manager import run_paper_session
    from app.reports.reporter import print_signal_report
    import dataclasses

    current = get_paper_capital(symbol)
    if current == 10_000.0 and capital != 10_000.0:
        set_paper_capital(symbol, capital)
        current = capital

    console.print(f"  Paper capital: [bold green]${current:,.2f}[/bold green]")

    df_1d  = get_candles(symbol, "1d",  limit=300)
    df_4h  = get_candles(symbol, "4h",  limit=500)
    df_1h  = get_candles(symbol, "1h",  limit=500)
    df_30m = get_candles(symbol, "30m", limit=500)

    if df_30m.empty:
        console.print(f"  [{WARN_COLOR}]No 30M data. Run Backfill first.[/{WARN_COLOR}]")
        return

    with console.status("[bold cyan]Running paper session...[/bold cyan]"):
        result = run_paper_session(
            symbol=symbol, df_1d=df_1d, df_4h=df_4h,
            df_1h=df_1h, df_30m=df_30m,
            capital=current, risk_pct=risk,
        )

    print_signal_report(result["signal"])

    console.print(f"\n  Paper capital: [bold green]${result['capital']:,.2f}[/bold green]")
    if result["trade_opened"]:
        console.print(f"  [green]Trade opened: #{result['trade_opened']}[/green]")
    for t in result["closed_trades"]:
        pnl = t.get("pnl", 0)
        c = "green" if pnl >= 0 else "red"
        console.print(f"  [{c}]Closed {t['direction']}: PnL=${pnl:,.2f}[/{c}]")

    sm = result["summary"]
    if sm and sm.get("total"):
        wr = (sm["wins"] or 0) / sm["total"] * 100
        console.print(
            f"\n  History: {sm['total']} trades  "
            f"WR={wr:.1f}%  Total PnL=${sm.get('total_pnl', 0):,.2f}"
        )


# ── 7. Train ──────────────────────────────────────────────────────────────────

def menu_train() -> None:
    _section_header("Train Models")

    symbol = _ask_symbol()
    console.print("\n  Which model to train?")
    console.print("    [1] BUY model only")
    console.print("    [2] SELL model only")
    console.print("    [3] Both BUY and SELL  [bold](recommended)[/bold]")

    choice = Prompt.ask("  Select", choices=["1", "2", "3"], default="3")
    targets = {
        "1": ["buy"],
        "2": ["sell"],
        "3": ["buy", "sell"],
    }[choice]

    console.print()
    from app.models.trainer import train_model

    for mt in targets:
        console.print(f"  Training [bold]{mt.upper()}[/bold] model...")
        try:
            m = train_model(symbol=symbol, model_type=mt)
            console.print(f"  [{OK_COLOR}]{mt.upper()} model trained[/{OK_COLOR}]")
            _info_row("Version",      m["version"])
            _info_row("Samples",      f"{m['samples']}  ({m['wins']}W / {m['losses']}L)")
            _info_row("Win rate",     f"{m['win_rate']*100:.1f}%")
            _info_row("CV F1",        f"{m['cv_f1_mean']:.4f} +/- {m['cv_f1_std']:.4f}")
            _info_row("Train acc.",   f"{m['train_accuracy']*100:.1f}%")
            console.print(f"\n  [dim]Run Validate to activate this model.[/dim]")
        except ValueError as exc:
            console.print(f"  [{WARN_COLOR}]{mt.upper()} cannot be trained: {exc}[/{WARN_COLOR}]")
        console.print()


# ── 8. Validate ───────────────────────────────────────────────────────────────

def menu_validate() -> None:
    _section_header("Validate & Activate Model")

    symbol = _ask_symbol()
    console.print("\n  Which model to validate?")
    console.print("    [1] BUY model")
    console.print("    [2] SELL model")
    console.print("    [3] Both")
    choice = Prompt.ask("  Select", choices=["1", "2", "3"], default="3")
    targets = {"1": ["buy"], "2": ["sell"], "3": ["buy", "sell"]}[choice]

    auto_accept = Confirm.ask(
        "  Auto-activate if validation passes?", default=True
    )
    console.print()

    from app.models.validator import validate_model, activate_model
    from app.db.connection import get_conn
    from pathlib import Path

    for mt in targets:
        console.print(f"  Validating [bold]{mt.upper()}[/bold] model...")
        with get_conn() as conn:
            row = conn.execute(
                "SELECT version, file_path FROM model_versions "
                "WHERE symbol=? AND model_type=? AND status='candidate' "
                "ORDER BY created_at DESC LIMIT 1",
                (symbol, mt),
            ).fetchone()

        if not row:
            console.print(f"  [{WARN_COLOR}]No candidate {mt} model. Train one first.[/{WARN_COLOR}]")
            continue

        version, fp = row
        fp = Path(fp)
        if not fp.exists():
            console.print(f"  [{ERR_COLOR}]Model file missing: {fp}[/{ERR_COLOR}]")
            continue

        report = validate_model(symbol=symbol, model_type=mt, candidate_path=fp)
        accepted = report.get("accepted", False)

        c = OK_COLOR if accepted else ERR_COLOR
        console.print(f"  Result: [{c}]{'ACCEPTED' if accepted else 'REJECTED'}[/{c}]")
        _info_row("Reason", report.get("reason", "—"))
        if report.get("candidate_f1"):
            _info_row("Candidate F1", f"{report['candidate_f1']:.4f}")
        if report.get("current_f1") is not None:
            _info_row("Current F1",   f"{report['current_f1']:.4f}")
            if report.get("f1_improvement") is not None:
                _info_row("Improvement",  f"{report['f1_improvement']:+.4f}")

        if accepted and auto_accept:
            activate_model(symbol, mt, version)
            console.print(f"  [{OK_COLOR}]Model {version} activated.[/{OK_COLOR}]")
        console.print()


# ── 9. Reports ────────────────────────────────────────────────────────────────

REPORT_MENU = [
    ("1", "General Overview",     "Data quality, signal, paper trades, models"),
    ("2", "Backtest Report",      "Detailed backtest performance metrics"),
    ("3", "Self-Learning Report", "Model analysis, mistakes, recommendations"),
    ("0", "Back",                 ""),
]


def menu_reports() -> None:
    while True:
        print_header()
        _section_header("Reports", clear=False)

        t = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
        t.add_column("Key",    style="bold yellow", width=4)
        t.add_column("Report", style=MENU_COLOR,    width=28)
        t.add_column("Hint",   style=HINT_COLOR,    width=42)
        for key, name, hint in REPORT_MENU:
            t.add_row(f"[{key}]", name, hint)
        console.print(Align.center(t))
        console.print()

        choice = Prompt.ask(
            "  [bold yellow]Choose report[/bold yellow]",
            choices=["1", "2", "3", "0"],
            show_choices=False,
        )

        if choice == "0":
            break

        symbol = _ask_symbol()
        console.print()

        from app.reports.reporter import print_general_report, print_backtest_report
        from app.reports.backtest_report import print_detailed_backtest_report
        from app.reports.learning_report import print_learning_report

        if choice == "1":
            print_general_report(symbol)
        elif choice == "2":
            print_detailed_backtest_report(symbol)
        elif choice == "3":
            print_learning_report(symbol)

        _pause()


# ── L. Live Mode ─────────────────────────────────────────────────────────────

def menu_live() -> None:
    _section_header("Live Mode — Auto-Signal Watcher")

    cfg = get_settings()
    has_token   = bool(getattr(cfg, "telegram_bot_token", ""))
    has_chat_id = bool(getattr(cfg, "telegram_chat_id", ""))

    # Telegram status
    if has_token and has_chat_id:
        console.print("  Telegram: [bold green]Configured[/bold green]")
    else:
        console.print("  Telegram: [bold red]NOT configured[/bold red]")
        console.print("\n  [yellow]To receive Telegram alerts you need to add to your .env:[/yellow]")
        console.print("  [dim]TELEGRAM_BOT_TOKEN=123456:ABC-your-bot-token[/dim]")
        console.print("  [dim]TELEGRAM_CHAT_ID=987654321[/dim]")
        console.print("\n  [dim]Steps to get these:[/dim]")
        console.print("  [dim]1. Message @BotFather on Telegram → /newbot → copy token[/dim]")
        console.print("  [dim]2. Start a chat with your bot[/dim]")
        console.print("  [dim]3. Visit: https://api.telegram.org/bot<TOKEN>/getUpdates[/dim]")
        console.print("  [dim]4. Find the 'id' field under 'chat' — that is your chat_id[/dim]")
        console.print()
        if not Confirm.ask("  Continue without Telegram?", default=False):
            return

    symbol  = _ask_symbol()
    capital = _ask_capital()
    risk    = _ask_risk()

    console.print("\n  Execution timeframe (candle close interval):")
    console.print("    [1] 30m   — signal every 30 minutes  [bold](recommended)[/bold]")
    console.print("    [2] 1h    — signal every 1 hour")
    console.print("    [3] 4h    — signal every 4 hours")
    tf_map = {"1": "30m", "2": "1h", "3": "4h"}
    tf_key = Prompt.ask("  Select", choices=list(tf_map), default="1")
    timeframe = tf_map[tf_key]

    heartbeat = IntPrompt.ask(
        "  Heartbeat every N candles (0 = off)",
        default=8,
    )

    dry_run = Confirm.ask(
        "  Dry-run mode? (signals only, no trades opened)",
        default=False,
    )

    console.print()
    console.print(Panel(
        f"[bold cyan]Live Mode Configuration[/bold cyan]\n\n"
        f"Symbol:     [bold yellow]{symbol}[/bold yellow]\n"
        f"Timeframe:  [bold]{timeframe}[/bold]   (signal at every {timeframe} candle close)\n"
        f"Capital:    [bold green]${capital:,.0f}[/bold green]\n"
        f"Risk:       [bold]{risk}%[/bold] per trade\n"
        f"Heartbeat:  {'every ' + str(heartbeat) + ' candles' if heartbeat > 0 else 'OFF'}\n"
        f"Dry-run:    {'YES — signals only, no trades' if dry_run else 'NO — trades will open'}",
        border_style="cyan",
    ))

    if not Confirm.ask("  Start Live Mode?", default=True):
        return

    console.print("\n  [dim]Press Ctrl+C to stop.[/dim]\n")

    from app.live.watcher import LiveWatcher
    watcher = LiveWatcher(
        symbol=symbol,
        exec_timeframe=timeframe,
        heartbeat_every=heartbeat,
        paper_capital=capital,
        risk_pct=risk,
        dry_run=dry_run,
    )
    watcher.run()


# ── S. Settings ───────────────────────────────────────────────────────────────

def menu_settings() -> None:
    _section_header("Settings")

    console.print(f"  Current symbol:  [bold yellow]{STATE['symbol']}[/bold yellow]")
    console.print(f"  Current capital: [bold green]${STATE['capital']:,.0f}[/bold green]")
    console.print(f"  Current risk:    [bold]{STATE['risk']}%[/bold]\n")

    STATE["symbol"]  = Prompt.ask("  Symbol",     default=STATE["symbol"]).upper()
    STATE["capital"] = FloatPrompt.ask("  Capital ($)", default=STATE["capital"])
    STATE["risk"]    = FloatPrompt.ask("  Risk % per trade", default=STATE["risk"])

    console.print(f"\n  [{OK_COLOR}]Settings updated.[/{OK_COLOR}]")


# ── Shared helpers ────────────────────────────────────────────────────────────

def _ask_symbol() -> str:
    return Prompt.ask("  Symbol", default=STATE["symbol"]).upper()


def _ask_capital() -> float:
    return FloatPrompt.ask("  Capital ($)", default=STATE["capital"])


def _ask_risk() -> float:
    return FloatPrompt.ask("  Risk % per trade", default=STATE["risk"])


def _ask_timeframes() -> list[str]:
    raw = Prompt.ask("  Timeframes (space-separated)", default="1d 4h 1h 30m")
    return raw.split()


def _section_header(title: str, clear: bool = True) -> None:
    if clear:
        print_header()
    console.print(Rule(f"[bold cyan]{title}[/bold cyan]", style="cyan"))
    console.print()


def _info_row(label: str, value: str) -> None:
    console.print(f"    [dim]{label}:[/dim]  {value}")


def _pause() -> None:
    console.print()
    Prompt.ask("  [dim]Press Enter to return to menu[/dim]", default="")


def _regime_color(regime: str) -> str:
    colors = {
        "bullish_trend":              "green",
        "accumulation":               "cyan",
        "bearish_trend":              "red",
        "distribution":               "magenta",
        "trap_zone":                  "yellow",
        "squeeze":                    "yellow",
        "high_volatility_liquidation": "red",
        "choppy":                     "dim",
    }
    return colors.get(regime, "white")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        main_menu()
    except KeyboardInterrupt:
        console.print("\n\n[dim]Interrupted. Goodbye.[/dim]\n")
