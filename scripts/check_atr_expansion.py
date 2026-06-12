"""Check ATR expansion stats during losing sideways/choppy scenarios."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from app.db.migrations import run_migrations; run_migrations()
from app.data.repository import get_candles
from app.ta.indicators import add_indicators
from app.utils.timeframes import str_to_ms
import numpy as np

SCENARIOS = [
    ("BTC SIDEWAYS 2023-Q3",  "BTCUSDT",  "2023-07-01", "2023-09-30"),
    ("ETH SIDEWAYS 2023-Q1",  "ETHUSDT",  "2023-01-01", "2023-03-31"),
    ("BTC BREAKOUT 2024-Q1",  "BTCUSDT",  "2024-01-01", "2024-03-31"),  # known over-trader
    ("BNB ATH-CRASH 2021-Q4", "BNBUSDT",  "2021-10-01", "2021-12-31"),
    ("BTC RECENT Q1-2025",    "BTCUSDT",  "2025-01-01", "2025-03-31"),
    # Winners for comparison
    ("BNB MEGA-BULL 2021-Q1", "BNBUSDT",  "2021-01-01", "2021-03-31"),
    ("ETH BEAR 2022-Q2",      "ETHUSDT",  "2022-04-01", "2022-06-30"),
]

for label, symbol, start, end in SCENARIOS:
    df = get_candles(symbol, "30m", start_ms=str_to_ms(start), end_ms=str_to_ms(end))
    if df.empty:
        print(f"{label}: NO DATA")
        continue
    df = add_indicators(df)
    atr = df["atr_expansion"].dropna()

    pct_above_18 = (atr > 1.8).mean() * 100
    pct_above_25 = (atr > 2.5).mean() * 100
    pct_above_15 = (atr > 1.5).mean() * 100

    print(f"\n{label} ({symbol} {start}→{end})")
    print(f"  ATR expansion — mean={atr.mean():.2f}  median={atr.median():.2f}  max={atr.max():.2f}")
    print(f"  % candles > 1.5x: {pct_above_15:.1f}%")
    print(f"  % candles > 1.8x: {pct_above_18:.1f}%")
    print(f"  % candles > 2.5x: {pct_above_25:.1f}%")
