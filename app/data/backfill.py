"""Historical candle backfill logic — Phase 1."""

from __future__ import annotations

import time
from typing import Any

from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
)

from app.data.binance_client import BinanceClient
from app.data.repository import (
    get_earliest_candle_time,
    get_latest_candle_time,
    upsert_candles,
)
from app.data.validators import validate_candles
from app.utils.logger import get_logger
from app.utils.timeframes import expected_candle_count, ms_to_dt, tf_to_ms

logger = get_logger(__name__)
console = Console()

_BATCH_SIZE = 1000  # Binance max klines per request


def _parse_klines(raw: list[list[Any]], symbol: str, timeframe: str) -> list[dict]:
    """Convert raw Binance kline lists into dicts for DB insertion."""
    rows = []
    for k in raw:
        rows.append({
            "symbol": symbol,
            "timeframe": timeframe,
            "open_time": int(k[0]),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
            "close_time": int(k[6]),
            "quote_volume": float(k[7]),
            "trades": int(k[8]),
            "taker_buy_base_volume": float(k[9]),
            "taker_buy_quote_volume": float(k[10]),
        })
    return rows


def backfill(
    symbol: str,
    timeframes: list[str],
    start_ms: int,
    end_ms: int,
    resume: bool = True,
) -> dict[str, dict]:
    """
    Download and store candles for each timeframe.

    Returns per-timeframe data quality reports.
    """
    symbol = symbol.upper()
    reports: dict[str, dict] = {}

    with BinanceClient() as client:
        # Verify symbol exists
        try:
            client.get_ticker_price(symbol)
        except Exception as exc:
            raise ValueError(f"Symbol {symbol!r} not found on Binance: {exc}") from exc

        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            for tf in timeframes:
                interval_ms = tf_to_ms(tf)
                tf_start = start_ms
                tf_end = end_ms

                # Resume: skip already-downloaded portion
                if resume:
                    latest = get_latest_candle_time(symbol, tf)
                    if latest is not None and latest >= tf_end:
                        console.print(f"[green]✓[/green] [{symbol} {tf}] Already complete — skipping")
                        reports[tf] = validate_candles(symbol, tf, start_ms, end_ms)
                        continue
                    if latest is not None and latest > tf_start:
                        tf_start = latest + interval_ms
                        console.print(
                            f"[yellow]↻[/yellow] [{symbol} {tf}] Resuming from "
                            f"{ms_to_dt(tf_start).strftime('%Y-%m-%d %H:%M')}"
                        )

                total_expected = expected_candle_count(tf_start, tf_end, tf)
                task = progress.add_task(
                    f"[{symbol} {tf}] Downloading ~{total_expected:,} candles",
                    total=total_expected or 1,
                )

                current_start = tf_start
                total_inserted = 0

                while current_start < tf_end:
                    try:
                        batch_raw = client.get_klines(
                            symbol=symbol,
                            interval=tf,
                            start_ms=current_start,
                            end_ms=tf_end,
                            limit=_BATCH_SIZE,
                        )
                    except Exception as exc:
                        logger.error("[%s %s] Fetch error: %s", symbol, tf, exc)
                        break

                    if not batch_raw:
                        break

                    rows = _parse_klines(batch_raw, symbol, tf)
                    inserted = upsert_candles(rows)
                    total_inserted += inserted

                    last_open_time = int(batch_raw[-1][0])
                    progress.advance(task, len(batch_raw))

                    if len(batch_raw) < _BATCH_SIZE:
                        break

                    current_start = last_open_time + interval_ms
                    time.sleep(0.12)  # respect rate limit

                progress.update(task, completed=total_expected or 1)
                console.print(
                    f"[green]✓[/green] [{symbol} {tf}] "
                    f"Inserted {total_inserted:,} new candles"
                )

                report = validate_candles(symbol, tf, start_ms, end_ms)
                reports[tf] = report
                _print_quality(tf, report)

    return reports


def update_latest(symbol: str, timeframes: list[str]) -> dict[str, int]:
    """Fetch only missing/latest candles for each timeframe."""
    symbol = symbol.upper()
    inserted_counts: dict[str, int] = {}

    with BinanceClient() as client:
        server_time = client.get_server_time()

        for tf in timeframes:
            interval_ms = tf_to_ms(tf)
            latest = get_latest_candle_time(symbol, tf)

            if latest is None:
                console.print(
                    f"[yellow]![/yellow] [{symbol} {tf}] No data found — run backfill first"
                )
                inserted_counts[tf] = 0
                continue

            fetch_start = latest + interval_ms
            # Round down to the close of the last fully-closed candle so we
            # never fetch the current open (unclosed) candle — its volume would
            # be near-zero and corrupt the vol_ma ratio check.
            end_ms = (server_time // interval_ms) * interval_ms - 1
            if fetch_start > end_ms:
                console.print(f"[green]✓[/green] [{symbol} {tf}] Already up to date")
                inserted_counts[tf] = 0
                continue

            try:
                batch_raw = client.get_all_klines(
                    symbol=symbol,
                    interval=tf,
                    start_ms=fetch_start,
                    end_ms=end_ms,
                )
            except Exception as exc:
                logger.error("[%s %s] Update error: %s", symbol, tf, exc)
                inserted_counts[tf] = 0
                continue

            rows = _parse_klines(batch_raw, symbol, tf)
            inserted = upsert_candles(rows)
            inserted_counts[tf] = inserted
            console.print(
                f"[green]✓[/green] [{symbol} {tf}] "
                f"Updated {inserted:,} new candles "
                f"(latest: {ms_to_dt(latest).strftime('%Y-%m-%d %H:%M')} → "
                f"{ms_to_dt(end_ms).strftime('%Y-%m-%d %H:%M')})"
            )

    return inserted_counts


def _print_quality(tf: str, report: dict) -> None:
    score = report["quality_score"]
    color = "green" if score >= 95 else "yellow" if score >= 80 else "red"
    console.print(
        f"  [{color}]Data quality: {score:.1f}%[/{color}] "
        f"({report['actual_candles']:,}/{report['expected_candles']:,} candles)"
    )
    for issue in report.get("issues", []):
        console.print(f"  [yellow]⚠[/yellow] {issue}")
