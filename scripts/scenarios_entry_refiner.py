"""
5m Entry Refinement - Scenario Walkthrough

Shows every possible outcome of the refiner with realistic BTC price data.
Run: python scenarios_entry_refiner.py
"""

import sys
import io
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import pandas as pd
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box
from rich.rule import Rule
from rich.text import Text

from app.ta.entry_refiner import refine_entry

console = Console()

# ── helpers ───────────────────────────────────────────────────────────────────

def make_candles(rows: list[dict]) -> pd.DataFrame:
    """Build a minimal 5m DataFrame from a list of {open,high,low,close} dicts."""
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"]).astype(float)


def run_scenario(
    title:       str,
    description: str,
    signal:      str,
    entry_price: float,
    stop_loss:   float,
    candles:     list[dict],
    expected:    str,
) -> None:
    df = make_candles(candles)
    refined, reason = refine_entry(signal, entry_price, stop_loss, df)

    direction_color = "green" if signal == "BUY" else "red"
    outcome_color   = {
        "5m_pullback_absorbed":  "bold green",
        "5m_bullish_confirmed":  "green",
        "5m_bounce_rejected":    "bold green",
        "5m_bearish_confirmed":  "green",
        "5m_entry_stale":        "yellow",
        "5m_fallback":           "dim",
        "5m_no_data":            "dim",
    }.get(reason, "white")

    saved = entry_price - refined if signal == "BUY" else refined - entry_price
    saved_str = f"[bold green]+{saved:.2f} better[/bold green]" if saved > 0 else "[dim]no change[/dim]"

    console.print()
    console.print(Panel(
        f"[bold]{title}[/bold]\n[dim]{description}[/dim]",
        border_style="cyan",
        padding=(0, 2),
    ))

    # Candle table
    t = Table(box=box.SIMPLE, show_header=True, header_style="bold dim")
    t.add_column("Candle", style="dim", width=8)
    t.add_column("Open",   justify="right")
    t.add_column("High",   justify="right")
    t.add_column("Low",    justify="right")
    t.add_column("Close",  justify="right")
    t.add_column("Dir",    justify="center")
    for i, row in df.iterrows():
        label = f"  C-{len(df)-1-i}" if i < len(df)-1 else "  LAST"
        bull = row["close"] > row["open"]
        dir_sym = "[green]▲[/green]" if bull else "[red]▼[/red]"
        t.add_row(label,
                  f"{row['open']:.2f}", f"{row['high']:.2f}",
                  f"{row['low']:.2f}",  f"{row['close']:.2f}", dir_sym)
    console.print(t)

    console.print(
        f"  Signal:        [{direction_color}]{signal}[/{direction_color}]\n"
        f"  30m entry:     {entry_price:.2f}   SL: {stop_loss:.2f}\n"
        f"  5m result:     [{outcome_color}]{reason}[/{outcome_color}]\n"
        f"  Refined entry: [bold]{refined:.2f}[/bold]   {saved_str}\n"
        f"  Expected:      [dim]{expected}[/dim]"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# SCENARIOS
# ═══════════════════════════════════════════════════════════════════════════════

console.print()
console.print(Rule("[bold cyan]5m Entry Refinement — Scenarios[/bold cyan]", style="cyan"))
console.print("[dim]  BTC entry zone ~$107,000 | SL ~$106,000 | risk dist ~$1,000[/dim]")

# ──────────────────────────────────────────────────────────────────────────────
# BUY SCENARIOS
# ──────────────────────────────────────────────────────────────────────────────

console.print()
console.print(Rule("[green]BUY Scenarios[/green]", style="green"))

run_scenario(
    title       = "BUY · Scenario 1 — Pullback Absorbed  ✓ BEST",
    description = (
        "30m candle closes at $107,000 (demand zone). During that 30m, a 5m wick "
        "stabbed down to $106,780 (below entry) — hunting stops — then closed back "
        "above. Last 5m is bullish and near entry. Refiner catches the dip and uses "
        "the candle low as entry, saving $70 per unit."
    ),
    signal      = "BUY",
    entry_price = 107_000.0,
    stop_loss   = 106_000.0,
    candles     = [
        {"open": 107_050, "high": 107_120, "low": 107_010, "close": 107_080},  # C-5
        {"open": 107_080, "high": 107_100, "low": 106_900, "close": 106_950},  # C-4
        {"open": 106_950, "high": 107_000, "low": 106_780, "close": 106_820},  # C-3  ← wick below entry
        {"open": 106_820, "high": 107_050, "low": 106_800, "close": 107_020},  # C-2  ← recovery
        {"open": 107_020, "high": 107_100, "low": 106_950, "close": 107_060},  # C-1
        {"open": 107_060, "high": 107_150, "low": 106_930, "close": 107_080},  # LAST ← bullish, near entry
    ],
    expected    = "5m_pullback_absorbed — entry refined to candle low (~$106,930)",
)

run_scenario(
    title       = "BUY · Scenario 2 — Bullish Confirmed  ✓ GOOD",
    description = (
        "30m closes at $107,000. The last 5m candles are steadily bullish right at "
        "the zone — no wick below entry, but price is green and sitting at the level. "
        "Good timing, no better price available. Entry stays at $107,000."
    ),
    signal      = "BUY",
    entry_price = 107_000.0,
    stop_loss   = 106_000.0,
    candles     = [
        {"open": 106_900, "high": 107_050, "low": 106_880, "close": 107_020},  # C-5
        {"open": 107_020, "high": 107_080, "low": 106_990, "close": 107_060},  # C-4
        {"open": 107_060, "high": 107_120, "low": 107_040, "close": 107_090},  # C-3
        {"open": 107_090, "high": 107_150, "low": 107_070, "close": 107_110},  # C-2
        {"open": 107_110, "high": 107_160, "low": 107_090, "close": 107_140},  # C-1
        {"open": 107_010, "high": 107_180, "low": 107_005, "close": 107_080},  # LAST ← bullish, near entry, low above entry
    ],
    expected    = "5m_bullish_confirmed — entry stays at $107,000",
)

run_scenario(
    title       = "BUY · Scenario 3 — Entry Stale  ⚠ CAUTION",
    description = (
        "30m closes at $107,000 (demand zone touched). But the 5m candles show price "
        "has already ripped $500+ above the zone. Entering at the 30m entry price "
        "would be unrealistic — the level is stale. Refiner flags it; entry stays "
        "at $107,000 (the paper fill uses it as-is, noted in the log)."
    ),
    signal      = "BUY",
    entry_price = 107_000.0,
    stop_loss   = 106_000.0,
    candles     = [
        {"open": 107_000, "high": 107_200, "low": 106_980, "close": 107_180},  # C-5
        {"open": 107_180, "high": 107_350, "low": 107_160, "close": 107_320},  # C-4
        {"open": 107_320, "high": 107_480, "low": 107_300, "close": 107_460},  # C-3
        {"open": 107_460, "high": 107_600, "low": 107_440, "close": 107_580},  # C-2
        {"open": 107_580, "high": 107_700, "low": 107_560, "close": 107_650},  # C-1
        {"open": 107_650, "high": 107_750, "low": 107_620, "close": 107_700},  # LAST ← ran >0.4% above entry
    ],
    expected    = "5m_entry_stale — entry stays at $107,000, flagged in log",
)

run_scenario(
    title       = "BUY · Scenario 4 — Fallback  — NEUTRAL",
    description = (
        "30m closes at $107,000. The 5m candles are mixed — price is near the zone "
        "but the last candle is bearish (red). No clear absorption. Refiner falls back "
        "to original entry. Trade still opens — 30m signal remains valid."
    ),
    signal      = "BUY",
    entry_price = 107_000.0,
    stop_loss   = 106_000.0,
    candles     = [
        {"open": 107_100, "high": 107_150, "low": 107_050, "close": 107_120},  # C-5
        {"open": 107_120, "high": 107_160, "low": 107_060, "close": 107_080},  # C-4  ← red
        {"open": 107_080, "high": 107_130, "low": 107_020, "close": 107_100},  # C-3
        {"open": 107_100, "high": 107_140, "low": 107_040, "close": 107_060},  # C-2  ← red
        {"open": 107_060, "high": 107_110, "low": 107_010, "close": 107_040},  # C-1  ← red
        {"open": 107_040, "high": 107_080, "low": 106_980, "close": 107_010},  # LAST ← red, near entry
    ],
    expected    = "5m_fallback — entry stays at $107,000, trade opens normally",
)

# ──────────────────────────────────────────────────────────────────────────────
# SELL SCENARIOS
# ──────────────────────────────────────────────────────────────────────────────

console.print()
console.print(Rule("[red]SELL Scenarios[/red]", style="red"))

run_scenario(
    title       = "SELL · Scenario 5 — Bounce Rejected  ✓ BEST",
    description = (
        "30m candle closes at $107,000 (supply zone). During that 30m, a 5m wick "
        "spiked up to $107,220 — squeezing late longs — then closed back below. "
        "Last 5m is bearish and near entry. Refiner uses the candle high as entry, "
        "giving a better short fill at $107,150 instead of $107,000."
    ),
    signal      = "SELL",
    entry_price = 107_000.0,
    stop_loss   = 108_000.0,
    candles     = [
        {"open": 106_950, "high": 107_020, "low": 106_900, "close": 106_980},  # C-5
        {"open": 106_980, "high": 107_100, "low": 106_950, "close": 107_050},  # C-4
        {"open": 107_050, "high": 107_220, "low": 107_020, "close": 107_080},  # C-3  ← wick above entry
        {"open": 107_080, "high": 107_150, "low": 106_990, "close": 107_020},  # C-2  ← rejection starts
        {"open": 107_020, "high": 107_080, "low": 106_960, "close": 106_980},  # C-1  ← bearish
        {"open": 107_080, "high": 107_150, "low": 106_940, "close": 106_990},  # LAST ← bearish, near entry
    ],
    expected    = "5m_bounce_rejected — entry refined to candle high (~$107,150)",
)

run_scenario(
    title       = "SELL · Scenario 6 — Bearish Confirmed  ✓ GOOD",
    description = (
        "30m closes at $107,000 (supply zone). Last 5m candles are consistently "
        "red right at the level, no wick above. Clean rejection without the squeeze. "
        "No better price to offer — entry stays at $107,000 with confirmation."
    ),
    signal      = "SELL",
    entry_price = 107_000.0,
    stop_loss   = 108_000.0,
    candles     = [
        {"open": 107_150, "high": 107_200, "low": 107_080, "close": 107_100},  # C-5
        {"open": 107_100, "high": 107_140, "low": 107_050, "close": 107_060},  # C-4
        {"open": 107_060, "high": 107_100, "low": 107_000, "close": 107_020},  # C-3
        {"open": 107_020, "high": 107_060, "low": 106_970, "close": 106_990},  # C-2
        {"open": 106_990, "high": 107_030, "low": 106_940, "close": 106_960},  # C-1
        {"open": 107_040, "high": 106_999, "low": 106_950, "close": 106_980},  # LAST ← bearish, near entry, high below entry
    ],
    expected    = "5m_bearish_confirmed — entry stays at $107,000",
)

run_scenario(
    title       = "SELL · Scenario 7 — Entry Stale  ⚠ CAUTION",
    description = (
        "30m closes at $107,000. But 5m candles show price already dumped $500 below "
        "the entry zone. Shorting at $107,000 when market is at $106,500 is unrealistic. "
        "Refiner flags it as stale — entry stays at $107,000 but logged for review."
    ),
    signal      = "SELL",
    entry_price = 107_000.0,
    stop_loss   = 108_000.0,
    candles     = [
        {"open": 107_000, "high": 107_050, "low": 106_800, "close": 106_820},  # C-5
        {"open": 106_820, "high": 106_860, "low": 106_620, "close": 106_650},  # C-4
        {"open": 106_650, "high": 106_680, "low": 106_480, "close": 106_500},  # C-3
        {"open": 106_500, "high": 106_540, "low": 106_340, "close": 106_360},  # C-2
        {"open": 106_360, "high": 106_400, "low": 106_200, "close": 106_220},  # C-1
        {"open": 106_220, "high": 106_260, "low": 106_080, "close": 106_100},  # LAST ← >0.4% below entry
    ],
    expected    = "5m_entry_stale — entry stays at $107,000, flagged in log",
)

# ──────────────────────────────────────────────────────────────────────────────
# SUMMARY
# ──────────────────────────────────────────────────────────────────────────────

console.print()
console.print(Rule("[bold cyan]Summary[/bold cyan]", style="cyan"))

summary = Table(box=box.ROUNDED, show_header=True, header_style="bold")
summary.add_column("Outcome",             style="bold",  width=28)
summary.add_column("Direction",           justify="center", width=8)
summary.add_column("5m Pattern",          width=30)
summary.add_column("Entry Effect",        width=22)

summary.add_row(
    "[bold green]5m_pullback_absorbed[/bold green]", "BUY",
    "Wick below entry → bullish close at zone",
    "[green]Better price (lower)[/green]",
)
summary.add_row(
    "[green]5m_bullish_confirmed[/green]", "BUY",
    "Green candle right at zone, no prior wick",
    "Original entry, high confidence",
)
summary.add_row(
    "[bold green]5m_bounce_rejected[/bold green]", "SELL",
    "Wick above entry → bearish close at zone",
    "[green]Better price (higher)[/green]",
)
summary.add_row(
    "[green]5m_bearish_confirmed[/green]", "SELL",
    "Red candle right at zone, no prior wick",
    "Original entry, high confidence",
)
summary.add_row(
    "[yellow]5m_entry_stale[/yellow]", "BUY/SELL",
    "Price moved >0.4% past entry zone",
    "[yellow]Original entry, logged[/yellow]",
)
summary.add_row(
    "[dim]5m_fallback[/dim]", "BUY/SELL",
    "Mixed/unclear 5m picture",
    "Original entry, no change",
)
summary.add_row(
    "[dim]5m_no_data[/dim]", "BUY/SELL",
    "Not enough 5m candles in DB",
    "Original entry, no change",
)

console.print(summary)
console.print()
console.print("[dim]  Tolerance band : ±0.20% of entry price  (\"near zone\" gate)[/dim]")
console.print("[dim]  Chase limit    :  0.40% past entry price (\"stale\" gate)[/dim]")
console.print("[dim]  Lookback window: last 6 five-minute candles (= 30 min)[/dim]")
console.print()
