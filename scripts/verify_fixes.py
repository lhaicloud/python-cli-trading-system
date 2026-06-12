"""
Targeted verification — 5 failing scenarios + 5 protected winners.
Baseline (pre-fix) vs current results shown side by side.
"""
import sys, io, time, datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from dotenv import load_dotenv; load_dotenv()
from app.db.migrations import run_migrations; run_migrations()
from app.backtesting.engine import run_backtest
from app.utils.timeframes import str_to_ms

CAPITAL  = 10_000.0
RISK_PCT = 1.0

SCENARIOS = [
    # ── 5 Failing (must improve) ─────────────────────────────────────────────
    ("FAIL", "BTC SIDEWAYS 2023-Q3",  "BTCUSDT", "2023-07-01", "2023-09-30"),
    ("FAIL", "BTC BREAKOUT 2024-Q1",  "BTCUSDT", "2024-01-01", "2024-03-31"),
    ("FAIL", "BTC RECENT Q1-2025",    "BTCUSDT", "2025-01-01", "2025-03-31"),
    ("FAIL", "ETH SIDEWAYS 2023-Q1",  "ETHUSDT", "2023-01-01", "2023-03-31"),
    ("FAIL", "BNB ATH-CRASH 2021-Q4", "BNBUSDT", "2021-10-01", "2021-12-31"),
    # ── 5 Protected Winners (must not regress) ────────────────────────────────
    ("WIN",  "BTC EARLY-BEAR 2022-Q1","BTCUSDT", "2022-01-01", "2022-03-31"),
    ("WIN",  "ETH BEAR 2022-Q2",      "ETHUSDT", "2022-04-01", "2022-06-30"),
    ("WIN",  "BNB MEGA-BULL 2021-Q1", "BNBUSDT", "2021-01-01", "2021-03-31"),
    ("WIN",  "SOL BREAKOUT 2024-Q1",  "SOLUSDT", "2024-01-01", "2024-03-31"),
    ("WIN",  "BTC OOS Q1-2026",       "BTCUSDT", "2026-01-01", "2026-03-31"),
]

BASELINE = {
    "BTC SIDEWAYS 2023-Q3":   {"trades": 8,  "net": -419.92, "wr": 12.5},
    "BTC BREAKOUT 2024-Q1":   {"trades": 23, "net": -173.74, "wr": 21.7},
    "BTC RECENT Q1-2025":     {"trades": 4,  "net": -199.40, "wr":  0.0},
    "ETH SIDEWAYS 2023-Q1":   {"trades": 3,  "net": -199.40, "wr":  0.0},
    "BNB ATH-CRASH 2021-Q4":  {"trades": 12, "net": -390.52, "wr": 16.7},
    "BTC EARLY-BEAR 2022-Q1": {"trades": 14, "net": 1483.77,  "wr": 64.3},
    "ETH BEAR 2022-Q2":       {"trades": 20, "net": 1754.25,  "wr": 60.0},
    "BNB MEGA-BULL 2021-Q1":  {"trades": 18, "net": 1981.28,  "wr": 55.6},
    "SOL BREAKOUT 2024-Q1":   {"trades": 18, "net":  464.09,  "wr": 33.3},
    "BTC OOS Q1-2026":        {"trades": 18, "net": 1007.00,  "wr": 44.4},
}

results = []
total_start = time.time()

for grp, label, symbol, start, end in SCENARIOS:
    print(f"\n{'─'*58}")
    print(f"  [{grp}] {label}  [{start} → {end}]")
    print(f"{'─'*58}")
    t0 = time.time()
    try:
        res = run_backtest(symbol, str_to_ms(start), str_to_ms(end), CAPITAL, RISK_PCT)
        m   = res["metrics"]
        elapsed = time.time() - t0
        b = BASELINE.get(label, {})
        delta_net = m["net_profit"] - b.get("net", 0)
        results.append((grp, label, m, b, delta_net))

        if m["total_trades"] == 0:
            print(f"  No trades  ({elapsed:.0f}s)")
        else:
            arrow = "▲" if delta_net > 0 else "▼"
            print(f"  Trades:   {m['total_trades']} (was {b.get('trades','?')})  Win%: {m['win_rate']*100:.1f}% (was {b.get('wr','?')}%)")
            print(f"  Net:      ${m['net_profit']:+,.2f} ({m['return_pct']:+.2f}%)  {arrow} ${delta_net:+,.2f} vs baseline")
            print(f"  PF:       {m['profit_factor']:.3f}   MaxDD: {m['max_drawdown_pct']:.2f}%  ({elapsed:.0f}s)")
    except Exception as e:
        elapsed = time.time() - t0
        print(f"  ERROR ({elapsed:.0f}s): {e}")
        import traceback; traceback.print_exc()

# ── Summary ──────────────────────────────────────────────────────────────────
print(f"\n\n{'='*65}")
print(f"  VERIFICATION SUMMARY")
print(f"{'='*65}")
print(f"  {'Scenario':<26} {'Trades':>6} {'Net%':>8} {'Delta':>10} {'Status':>10}")
print(f"  {'─'*26} {'─'*6} {'─'*8} {'─'*10} {'─'*10}")

fail_improved = 0
win_regressed = 0

for grp, label, m, b, delta_net in results:
    t = m["total_trades"]
    net_pct = m["return_pct"]
    tag = ""
    if grp == "FAIL":
        if m["net_profit"] > 0:
            tag = "✓ FIXED"
            fail_improved += 1
        elif delta_net > 0:
            tag = "~ BETTER"
            fail_improved += 1
        else:
            tag = "✗ STILL LOSS"
    else:
        if m["net_profit"] <= 0:
            tag = "✗ REGRESSED"
            win_regressed += 1
        elif delta_net < -200:
            tag = "~ WATCH"
        else:
            tag = "✓ OK"
    delta_str = f"${delta_net:+,.0f}"
    print(f"  {label:<26} {t:>6} {net_pct:>+7.2f}% {delta_str:>10} {tag:>10}")

print(f"\n  Failing scenarios improved: {fail_improved}/5")
print(f"  Winners regressed:          {win_regressed}/5")
total_delta = sum(d for _, _, _, _, d in results)
print(f"  Net delta vs baseline:      ${total_delta:+,.2f}")
print(f"  Runtime:                    {(time.time()-total_start)/60:.1f} min")
print(f"{'='*65}")
print("\nDone.")
