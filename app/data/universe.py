"""
Universe manager — discovers top coins by volume from Binance and syncs
them into the local DB via backfill.

Public API
----------
fetch_top_symbols(n, quote, min_volume_usd)
    → list[str] — top N symbols from Binance 24h ticker, filtered and ranked.

get_db_symbols()
    → set[str] — symbols already backfilled in the local DB.

sync_universe(n, quote, start, timeframes, min_volume_usd, dry_run)
    → dict — summary of new symbols discovered and backfilled.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from rich.console import Console

from app.data.binance_client import BinanceClient
from app.data.backfill import backfill
from app.db.connection import get_conn
from app.utils.logger import get_logger
from app.utils.timeframes import str_to_ms

logger  = get_logger(__name__)
console = Console()

# Timeframes required for the full signal pipeline
DEFAULT_TIMEFRAMES = ["1d", "4h", "1h", "30m", "12h", "1w"]

# Minimum listing age in days — avoids coins with insufficient history
MIN_LISTING_AGE_DAYS = 365  # need 52+ weekly candles for the 1W prefilter

# Token substrings that identify leveraged / synthetic products to exclude
_EXCLUDE_PATTERNS = [
    "UP", "DOWN", "BULL", "BEAR",
    "3L", "3S", "2L", "2S", "5L", "5S",
]

# Stablecoin base assets to exclude from USDT pairs
_STABLECOINS = {
    "USDC", "BUSD", "TUSD", "FDUSD", "DAI", "USDD", "USDP",
    "FRAX", "LUSD", "CRVUSD", "GUSD", "SUSD", "PYUSD",
    "USD1", "USDE", "USDS", "USDX", "AUSD", "CUSD",
}


def fetch_top_symbols(
    n: int = 50,
    quote: str = "USDT",
    min_volume_usd: float = 50_000_000,   # $50M 24h volume floor
) -> list[str]:
    """
    Return the top N symbols by 24h quote volume from Binance.

    Filters applied:
    - Quote currency must match `quote` (default USDT)
    - Excludes leveraged tokens (UP/DOWN/BULL/BEAR/3L/3S …)
    - Excludes stablecoin base assets (USDC, BUSD, …)
    - Minimum 24h quote volume of `min_volume_usd`

    Returns symbols sorted descending by 24h quote volume.
    """
    client = BinanceClient()
    try:
        tickers = client._get("/fapi/v1/ticker/24hr")
    finally:
        client.close()

    candidates = []
    for t in tickers:
        sym = t.get("symbol", "")

        # Must end with the quote currency
        if not sym.endswith(quote):
            continue

        base = sym[: -len(quote)]

        # Exclude stablecoins
        if base in _STABLECOINS:
            continue

        # Exclude leveraged / synthetic tokens
        if any(pat in base for pat in _EXCLUDE_PATTERNS):
            continue

        # Volume floor
        vol = float(t.get("quoteVolume", 0))
        if vol < min_volume_usd:
            continue

        candidates.append((sym, vol))

    candidates.sort(key=lambda x: x[1], reverse=True)
    return [sym for sym, _ in candidates[:n]]


def get_db_symbols(min_candles: int = 5_000) -> set[str]:
    """Return the set of symbols already in the local DB with sufficient data."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT symbol FROM candles
            WHERE timeframe='30m'
            GROUP BY symbol
            HAVING COUNT(*) >= ?
        """, (min_candles,)).fetchall()
    return {r[0] for r in rows}


def _earliest_listing_ms(symbol: str, client: BinanceClient) -> int | None:
    """
    Estimate a coin's listing date by fetching the oldest available 1w candle.
    Returns open_time of the first weekly candle, or None on error.
    """
    try:
        klines = client.get_klines(symbol, "1w", start_ms=0, limit=1)
        if klines:
            return int(klines[0][0])
    except Exception:
        pass
    return None


def sync_universe(
    n: int = 50,
    quote: str = "USDT",
    start: str = "2021-01-01",
    timeframes: list[str] | None = None,
    min_volume_usd: float = 50_000_000,
    min_listing_age_days: int = MIN_LISTING_AGE_DAYS,
    dry_run: bool = False,
) -> dict:
    """
    Fetch the top N coins from Binance and backfill any that are not yet
    in the local DB.

    Parameters
    ----------
    n                   : How many top-volume coins to consider.
    quote               : Quote currency (default "USDT").
    start               : Backfill start date for new coins (YYYY-MM-DD).
    timeframes          : Timeframes to backfill (default: all 6 pipeline TFs).
    min_volume_usd      : Minimum 24h quote volume to qualify.
    min_listing_age_days: Skip coins listed fewer than this many days ago.
    dry_run             : If True, print what would be backfilled but do nothing.

    Returns
    -------
    dict with keys: top_symbols, db_symbols, new_symbols, skipped, backfilled
    """
    tfs      = timeframes or DEFAULT_TIMEFRAMES
    start_ms = str_to_ms(start)
    end_ms   = int(time.time() * 1000)
    now_ms   = end_ms
    min_age_ms = min_listing_age_days * 86_400_000

    console.print(f"\n[bold cyan]Universe Sync[/bold cyan]  "
                  f"top={n}  quote={quote}  vol≥${min_volume_usd/1e6:.0f}M  "
                  f"age≥{min_listing_age_days}d  start={start}")

    # 1. Fetch top symbols from Binance
    console.print("  Fetching 24h ticker from Binance...")
    top_symbols = fetch_top_symbols(n=n, quote=quote, min_volume_usd=min_volume_usd)
    console.print(f"  [green]✓[/green] {len(top_symbols)} qualifying symbols from Binance top-{n}")

    # 2. Check what's already in the DB
    db_symbols  = get_db_symbols()
    new_symbols = [s for s in top_symbols if s not in db_symbols]

    console.print(f"  Already in DB: {len(db_symbols)}  │  New: {len(new_symbols)}")

    if not new_symbols:
        console.print("  [green]✓ Universe up to date — nothing to backfill[/green]")
        return {
            "top_symbols": top_symbols,
            "db_symbols":  list(db_symbols),
            "new_symbols": [],
            "skipped":     [],
            "backfilled":  [],
        }

    # 3. Age-filter and backfill new coins
    client    = BinanceClient()
    skipped   = []
    backfilled= []

    try:
        for sym in new_symbols:
            # Age check — skip coins listed too recently
            listing_ms = _earliest_listing_ms(sym, client)
            if listing_ms is not None:
                age_ms = now_ms - listing_ms
                if age_ms < min_age_ms:
                    age_days = age_ms // 86_400_000
                    console.print(f"  [yellow]SKIP[/yellow] {sym} — only {age_days}d old "
                                  f"(need {min_listing_age_days}d)")
                    skipped.append({"symbol": sym, "reason": f"too new ({age_days}d)"})
                    continue

            if dry_run:
                console.print(f"  [dim]DRY-RUN[/dim] would backfill {sym} from {start}")
                backfilled.append(sym)
                continue

            console.print(f"  [cyan]Backfilling[/cyan] {sym} from {start}...")
            try:
                backfill(sym, tfs, start_ms, end_ms)
                backfilled.append(sym)
                console.print(f"  [green]✓[/green] {sym} backfilled")
            except Exception as exc:
                logger.error("Backfill failed for %s: %s", sym, exc)
                console.print(f"  [red]ERROR[/red] {sym}: {exc}")
                skipped.append({"symbol": sym, "reason": str(exc)})
    finally:
        client.close()

    console.print(
        f"\n  [bold green]Done.[/bold green]  "
        f"Backfilled: {len(backfilled)}  │  Skipped: {len(skipped)}"
    )

    return {
        "top_symbols": top_symbols,
        "db_symbols":  list(db_symbols),
        "new_symbols": new_symbols,
        "skipped":     skipped,
        "backfilled":  backfilled,
    }
