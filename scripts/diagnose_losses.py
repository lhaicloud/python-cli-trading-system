"""
Diagnostic: show trade-by-trade timing, market_regime, and confidence
for each failing scenario. Helps calibrate circuit breaker and regime filters.
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from dotenv import load_dotenv; load_dotenv()
from app.db.migrations import run_migrations; run_migrations()
from app.backtesting.engine import run_backtest
from app.utils.timeframes import str_to_ms

CAPITAL  = 10_000.0
RISK_PCT = 1.0

FAILING = [
    ("BTC SIDEWAYS 2023-Q3",  "BTCUSDT", "2023-07-01", "2023-09-30"),
    ("BTC BREAKOUT 2024-Q1",  "BTCUSDT", "2024-01-01", "2024-03-31"),
    ("BTC RECENT Q1-2025",    "BTCUSDT", "2025-01-01", "2025-03-31"),
    ("ETH SIDEWAYS 2023-Q1",  "ETHUSDT", "2023-01-01", "2023-03-31"),
    ("BNB ATH-CRASH 2021-Q4", "BNBUSDT", "2021-10-01", "2021-12-31"),
    ("BTC OOS Q2-2025",       "BTCUSDT", "2025-04-01", "2025-06-30"),
]

import datetime

for label, symbol, start, end in FAILING:
    print(f"\n{'='*60}")
    print(f"  {label}  [{start} → {end}]")
    print(f"{'='*60}")
    res = run_backtest(symbol, str_to_ms(start), str_to_ms(end), CAPITAL, RISK_PCT)
    trades = res["trades"]
    if not trades:
        print("  No trades")
        continue

    # Sort by entry_time
    trades.sort(key=lambda t: t["entry_time"])

    print(f"  {'#':>2}  {'Entry':>20}  {'Exit':>20}  {'PnL':>8}  {'Conf':>5}  {'Regime':<20}  Gap30m")
    print(f"  {'─'*2}  {'─'*20}  {'─'*20}  {'─'*8}  {'─'*5}  {'─'*20}  {'─'*5}")

    prev_exit_ms = None
    consec = 0
    for idx, t in enumerate(trades):
        entry_dt = datetime.datetime.utcfromtimestamp(t["entry_time"] / 1000).strftime("%m-%d %H:%M")
        exit_dt  = datetime.datetime.utcfromtimestamp(t["exit_time"]  / 1000).strftime("%m-%d %H:%M")
        pnl = t["pnl"]
        conf = t.get("signal_confidence", 0)
        feats = t.get("features", {})
        if isinstance(feats, str):
            import json; feats = json.loads(feats) if feats else {}
        regime = feats.get("market_regime", "?")
        outcome = "W" if pnl > 0 else "L"

        # Gap in 30m candles since last exit
        if prev_exit_ms is not None:
            gap_ms = t["entry_time"] - prev_exit_ms
            gap_candles = gap_ms // (30 * 60 * 1000)
        else:
            gap_candles = 0

        if pnl <= 0:
            consec += 1
        else:
            consec = 0

        flag = f" ← consec={consec}" if consec >= 3 else ""
        print(f"  {idx+1:>2}  {entry_dt:>20}  {exit_dt:>20}  {pnl:>+8.2f} ({outcome})  {conf:>5.1f}  {str(regime):<20}  {gap_candles:>5}{flag}")
        prev_exit_ms = t["exit_time"]

    m = res["metrics"]
    print(f"\n  Net: ${m['net_profit']:+,.2f}  WR: {m['win_rate']*100:.1f}%  Trades: {m['total_trades']}")

print("\nDone.")
