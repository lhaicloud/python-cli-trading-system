"""
Trade analysis report — everything the paper_trades table can tell us.

Sections:
  1. Overall performance (closed trades)
  2. Open positions
  3. Per-symbol / per-direction breakdown
  4. Breakdown by setup type, market regime and UTC entry hour
  5. MFE/MAE diagnostics (instant losers, stop quality)
  6. Capital reconciliation (per-symbol equity vs sum of closed PnL)
  7. Data hygiene (bad close_times, stale candle feeds)

Run:  python main.py analyze-trades
"""

from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime, timezone

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from app.config import get_settings
from app.db.connection import get_conn

console = Console()

# MFE below this fraction of the risk distance counts as "never went our way"
_INSTANT_LOSER_MFE_FRACTION = 0.10
# A symbol with an open trade whose last 30m candle is older than this is stale
_STALE_CANDLE_MS = 2 * 60 * 60 * 1000


def _as_ms(v) -> int | None:
    """Legacy rows stored close_time as TEXT — coerce defensively."""
    try:
        return int(v) if v is not None else None
    except (ValueError, TypeError):
        return None


def _fmt_ts(ms) -> str:
    ms = _as_ms(ms)
    if not ms or ms < 1_000_000_000_000:  # epoch-ish garbage (< Sep 2001)
        return "—"
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _money(v: float) -> str:
    color = "green" if v >= 0 else "red"
    return f"[{color}]{'+' if v >= 0 else ''}{v:,.2f}[/{color}]"


def _load_trades() -> list[dict]:
    sql = """
        SELECT pt.*, s.setup_type AS sig_setup_type, s.market_regime AS sig_regime
        FROM paper_trades pt
        LEFT JOIN signals s ON s.id = pt.signal_id
        ORDER BY pt.open_time
    """
    with get_conn() as conn:
        rows = conn.execute(sql).fetchall()
    return [dict(r) for r in rows]


def _bucket_stats(trades: list[dict]) -> dict:
    wins   = [t for t in trades if (t["pnl"] or 0) > 0]
    losses = [t for t in trades if (t["pnl"] or 0) <= 0]
    gross_win  = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    n = len(trades)
    return {
        "n":          n,
        "wins":       len(wins),
        "losses":     len(losses),
        "win_rate":   len(wins) / n * 100 if n else 0.0,
        "pnl":        sum(t["pnl"] or 0 for t in trades),
        "avg_win":    gross_win / len(wins) if wins else 0.0,
        "avg_loss":   -gross_loss / len(losses) if losses else 0.0,
        "pf":         gross_win / gross_loss if gross_loss > 0 else float("inf"),
        "expectancy": sum(t["pnl"] or 0 for t in trades) / n if n else 0.0,
    }


def print_trade_analysis() -> None:
    cfg = get_settings()
    trades = _load_trades()
    if not trades:
        console.print("[yellow]No paper trades found.[/yellow]")
        return

    closed = [t for t in trades if t["status"] != "open"]
    opened = [t for t in trades if t["status"] == "open"]

    # ── 1. Overall ────────────────────────────────────────────────────────────
    s = _bucket_stats(closed)
    pf_str = "inf" if s["pf"] == float("inf") else f"{s['pf']:.2f}"
    console.print()
    console.print(Panel(
        f"[bold]Paper Trade Analysis[/bold]  —  "
        f"{len(closed)} closed / {len(opened)} open",
        border_style="blue",
    ))

    perf = Table(title="Overall (closed trades)", box=box.SIMPLE_HEAD)
    perf.add_column("Metric")
    perf.add_column("Value", justify="right")
    perf.add_row("Total PnL",      _money(s["pnl"]))
    perf.add_row("Win / Loss",     f"{s['wins']} / {s['losses']}")
    perf.add_row("Win Rate",       f"{s['win_rate']:.1f}%")
    perf.add_row("Profit Factor",  pf_str)
    perf.add_row("Avg Win",        f"${s['avg_win']:,.2f}")
    perf.add_row("Avg Loss",       f"${s['avg_loss']:,.2f}")
    perf.add_row("Expectancy",     f"${s['expectancy']:,.2f}/trade")
    console.print(perf)

    # ── 2. Open positions ─────────────────────────────────────────────────────
    if opened:
        ot = Table(title="Open Positions", box=box.SIMPLE_HEAD)
        for col in ("ID", "Symbol", "Dir", "Entry", "SL", "TP", "Opened", "uPnL"):
            ot.add_column(col, justify="right" if col in ("Entry", "SL", "TP", "uPnL") else "left")
        for t in opened:
            ot.add_row(
                str(t["id"]), t["symbol"], t["direction"],
                f"{t['entry_price']:.4f}", f"{t['stop_loss']:.4f}",
                f"{t['take_profit']:.4f}", _fmt_ts(t["open_time"]),
                _money(t["pnl"] or 0),
            )
        console.print(ot)

    # ── 3. Per-symbol / per-direction ─────────────────────────────────────────
    def _grouped(key_fn, title: str) -> None:
        groups: dict[str, list[dict]] = defaultdict(list)
        for t in closed:
            groups[key_fn(t) or "—"].append(t)
        tbl = Table(title=title, box=box.SIMPLE_HEAD)
        for col in ("Group", "Trades", "W/L", "Win Rate", "PnL", "PF"):
            tbl.add_column(col, justify="right" if col != "Group" else "left")
        for key in sorted(groups, key=lambda k: _bucket_stats(groups[k])["pnl"]):
            g = _bucket_stats(groups[key])
            gpf = "inf" if g["pf"] == float("inf") else f"{g['pf']:.2f}"
            tbl.add_row(
                key, str(g["n"]), f"{g['wins']}/{g['losses']}",
                f"{g['win_rate']:.0f}%", _money(g["pnl"]), gpf,
            )
        console.print(tbl)

    _grouped(lambda t: t["symbol"], "By Symbol")
    _grouped(lambda t: t["direction"], "By Direction")
    _grouped(lambda t: t.get("sig_setup_type"), "By Setup Type")
    _grouped(lambda t: t.get("sig_regime"), "By Market Regime (at signal)")
    _grouped(
        lambda t: f"{datetime.fromtimestamp(t['open_time'] / 1000, tz=timezone.utc).hour:02d}:xx UTC",
        "By Entry Hour (UTC)",
    )

    # ── 4. MFE/MAE diagnostics ────────────────────────────────────────────────
    stopped = [t for t in closed if t["status"] == "stopped"]
    instant = [
        t for t in stopped
        if (t["max_favorable_excursion"] or 0)
        < _INSTANT_LOSER_MFE_FRACTION * abs(t["entry_price"] - t["stop_loss"])
    ]
    console.print("\n[bold]Entry Quality (stopped trades)[/bold]")
    if stopped:
        console.print(
            f"  {len(instant)}/{len(stopped)} stopped trades had MFE < "
            f"{_INSTANT_LOSER_MFE_FRACTION:.0%} of risk distance — "
            f"price never moved in our favour (entry timing problem, not stop placement)."
        )
        if len(instant) / len(stopped) > 0.5:
            console.print(
                "  [yellow]WARN Majority of losers are instant losers — consider entry "
                "confirmation (limit at zone edge / rejection wick) before committing.[/yellow]"
            )
    else:
        console.print("  No stopped trades.")

    # ── 5. Capital reconciliation ─────────────────────────────────────────────
    recon = Table(title="Capital Reconciliation", box=box.SIMPLE_HEAD)
    for col in ("Symbol", "Closed PnL", "Expected", "Actual", "Drift"):
        recon.add_column(col, justify="right" if col != "Symbol" else "left")

    with get_conn() as conn:
        cap_rows = conn.execute(
            "SELECT key, value FROM app_settings WHERE key LIKE 'paper_capital_%'"
        ).fetchall()
    capitals = {r[0].replace("paper_capital_", "").upper(): float(r[1]) for r in cap_rows}

    pnl_by_symbol: dict[str, float] = defaultdict(float)
    for t in closed:
        pnl_by_symbol[t["symbol"]] += t["pnl"] or 0

    total_drift = 0.0
    for sym in sorted(set(capitals) | set(pnl_by_symbol)):
        actual   = capitals.get(sym)
        expected = cfg.default_capital + pnl_by_symbol.get(sym, 0.0)
        drift    = (actual - expected) if actual is not None else 0.0
        total_drift += abs(drift)
        recon.add_row(
            sym,
            _money(pnl_by_symbol.get(sym, 0.0)),
            f"${expected:,.2f}",
            f"${actual:,.2f}" if actual is not None else "—",
            _money(drift) if abs(drift) >= 0.01 else "[dim]0.00[/dim]",
        )
    console.print(recon)
    if total_drift > 1:
        console.print(
            f"  [yellow]WARN Total |drift| = ${total_drift:,.2f} — capital was updated "
            f"outside the normal close path (manual closes / double-updates).[/yellow]"
        )

    # ── 6. Data hygiene ───────────────────────────────────────────────────────
    console.print("\n[bold]Data Hygiene[/bold]")
    bad_close = [
        t for t in closed
        if (_as_ms(t["close_time"]) or 0) and (_as_ms(t["close_time"]) or 0) < (_as_ms(t["open_time"]) or 0)
    ]
    # Legacy manual-close scripts wrote close_time as ISO TEXT, not epoch ms
    text_close = [
        t for t in closed
        if t["close_time"] is not None and _as_ms(t["close_time"]) is None
    ]
    if bad_close:
        ids = ", ".join(str(t["id"]) for t in bad_close)
        console.print(
            f"  [yellow]WARN {len(bad_close)} trade(s) with close_time earlier than "
            f"open_time (ids: {ids}) — written by legacy manual-close scripts.[/yellow]"
        )
    if text_close:
        ids = ", ".join(str(t["id"]) for t in text_close)
        console.print(
            f"  [yellow]WARN {len(text_close)} trade(s) with TEXT close_time instead of "
            f"epoch ms (ids: {ids}) — run scripts/repair_paper_trades.py to fix.[/yellow]"
        )
    if not bad_close and not text_close:
        console.print("  [green]OK All close_times are sane.[/green]")

    now_ms = int(time.time() * 1000)
    for t in opened:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT MAX(open_time) FROM candles WHERE symbol=? AND timeframe='30m'",
                (t["symbol"],),
            ).fetchone()
        last_candle = row[0] if row else None
        if last_candle and now_ms - last_candle > _STALE_CANDLE_MS:
            age_h = (now_ms - last_candle) / 3_600_000
            console.print(
                f"  [red]FAIL Open trade #{t['id']} ({t['symbol']}) — last 30m candle "
                f"is {age_h:.1f}h old. SL/TP detection may be blind.[/red]"
            )
    console.print()
