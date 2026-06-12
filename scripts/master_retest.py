"""
Master Retest — all previously completed scenarios, run fresh with current strategy.
Total: 4 BTC original + 8 BTC extended + 7 ETH + 4 OOS + 6 SOL + 6 BNB = 35 scenarios
"""
import sys, io, time, datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from dotenv import load_dotenv; load_dotenv()
from app.db.migrations import run_migrations; run_migrations()
from app.backtesting.engine import run_backtest
from app.utils.timeframes import str_to_ms

def ms(date_str):
    return int(datetime.datetime.strptime(date_str, "%Y-%m-%d")
               .replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)

CAPITAL  = 10_000.0
RISK_PCT = 1.0

ALL_SCENARIOS = [
    # ── BTC Original 4 ──────────────────────────────────────────────
    ("BTC",  "BULL Q1-2023",        "BTCUSDT", "2023-01-01", "2023-03-31"),
    ("BTC",  "BEAR Q2-2022",        "BTCUSDT", "2022-04-01", "2022-06-30"),
    ("BTC",  "SIDEWAYS Q3-2024",    "BTCUSDT", "2024-07-01", "2024-09-30"),
    ("BTC",  "RECENT Q1-2025",      "BTCUSDT", "2025-01-01", "2025-03-31"),
    # ── BTC Extended 8 ──────────────────────────────────────────────
    ("BTC",  "MEGA-BULL 2021-Q1",   "BTCUSDT", "2021-01-01", "2021-03-31"),
    ("BTC",  "ATH-TOP 2021-Q4",     "BTCUSDT", "2021-10-01", "2021-12-31"),
    ("BTC",  "EARLY-BEAR 2022-Q1",  "BTCUSDT", "2022-01-01", "2022-03-31"),
    ("BTC",  "DEAD-CAT 2022-Q3",    "BTCUSDT", "2022-07-01", "2022-09-30"),
    ("BTC",  "SIDEWAYS 2023-Q3",    "BTCUSDT", "2023-07-01", "2023-09-30"),
    ("BTC",  "BREAKOUT 2024-Q1",    "BTCUSDT", "2024-01-01", "2024-03-31"),
    ("BTC",  "PULLBACK 2024-Q2",    "BTCUSDT", "2024-04-01", "2024-06-30"),
    ("BTC",  "BULL-ATH 2024-Q4",    "BTCUSDT", "2024-10-01", "2024-12-31"),
    # ── BTC OOS 4 (Q2-2025 to Q1-2026) ─────────────────────────────
    ("BTC",  "OOS Q2-2025",         "BTCUSDT", "2025-04-01", "2025-06-30"),
    ("BTC",  "OOS Q3-2025",         "BTCUSDT", "2025-07-01", "2025-09-30"),
    ("BTC",  "OOS Q4-2025",         "BTCUSDT", "2025-10-01", "2025-12-31"),
    ("BTC",  "OOS Q1-2026",         "BTCUSDT", "2026-01-01", "2026-03-31"),
    # ── ETH 7 ───────────────────────────────────────────────────────
    ("ETH",  "MEGA-BULL 2021-Q1",   "ETHUSDT", "2021-01-01", "2021-03-31"),
    ("ETH",  "ATH-TOP 2021-Q4",     "ETHUSDT", "2021-10-01", "2021-12-31"),
    ("ETH",  "BEAR 2022-Q2",        "ETHUSDT", "2022-04-01", "2022-06-30"),
    ("ETH",  "SIDEWAYS 2023-Q1",    "ETHUSDT", "2023-01-01", "2023-03-31"),
    ("ETH",  "BULL 2024-Q1",        "ETHUSDT", "2024-01-01", "2024-03-31"),
    ("ETH",  "SIDEWAYS 2024-Q3",    "ETHUSDT", "2024-07-01", "2024-09-30"),
    ("ETH",  "CRASH 2025-Q1",       "ETHUSDT", "2025-01-01", "2025-03-31"),
    # ── SOL 6 ───────────────────────────────────────────────────────
    ("SOL",  "MEGA-BULL 2021-Q1",   "SOLUSDT", "2021-01-01", "2021-03-31"),
    ("SOL",  "ATH-CRASH 2021-Q4",   "SOLUSDT", "2021-10-01", "2021-12-31"),
    ("SOL",  "BEAR 2022-Q2",        "SOLUSDT", "2022-04-01", "2022-06-30"),
    ("SOL",  "RECOVERY 2023-Q1",    "SOLUSDT", "2023-01-01", "2023-03-31"),
    ("SOL",  "BREAKOUT 2024-Q1",    "SOLUSDT", "2024-01-01", "2024-03-31"),
    ("SOL",  "PULLBACK 2024-Q3",    "SOLUSDT", "2024-07-01", "2024-09-30"),
    # ── BNB 6 ───────────────────────────────────────────────────────
    ("BNB",  "MEGA-BULL 2021-Q1",   "BNBUSDT", "2021-01-01", "2021-03-31"),
    ("BNB",  "ATH-CRASH 2021-Q4",   "BNBUSDT", "2021-10-01", "2021-12-31"),
    ("BNB",  "BEAR 2022-Q2",        "BNBUSDT", "2022-04-01", "2022-06-30"),
    ("BNB",  "RECOVERY 2023-Q1",    "BNBUSDT", "2023-01-01", "2023-03-31"),
    ("BNB",  "BREAKOUT 2024-Q1",    "BNBUSDT", "2024-01-01", "2024-03-31"),
    ("BNB",  "PULLBACK 2024-Q3",    "BNBUSDT", "2024-07-01", "2024-09-30"),
]

results   = []
errors    = []
run_start = time.time()

print(f"\n{'='*65}")
print(f"  MASTER RETEST — {len(ALL_SCENARIOS)} scenarios across 4 assets")
print(f"  Capital: ${CAPITAL:,.0f}  Risk: {RISK_PCT}% per trade")
print(f"{'='*65}")

for asset, label, symbol, start, end in ALL_SCENARIOS:
    full_label = f"{asset} {label}"
    print(f"\n{'─'*60}")
    print(f"  [{asset}] {label}  [{start} → {end}]")
    print(f"{'─'*60}")
    t0 = time.time()
    try:
        result = run_backtest(
            symbol=symbol,
            start_ms=ms(start),
            end_ms=ms(end),
            initial_capital=CAPITAL,
            risk_pct=RISK_PCT,
        )
        elapsed = time.time() - t0
        m = result["metrics"]
        trades = result["trades"]
        results.append((asset, full_label, m))

        if m['total_trades'] == 0:
            print(f"  No trades generated  ({elapsed:.0f}s)")
        else:
            win_pct = m['win_rate'] * 100
            profit  = "PROFIT" if m['net_profit'] > 0 else "LOSS"
            print(f"  Trades:        {m['total_trades']}   ({m['win_count']}W / {m['loss_count']}L)")
            print(f"  Win rate:      {win_pct:.1f}%")
            print(f"  Profit factor: {m['profit_factor']:.3f}")
            print(f"  Net profit:    ${m['net_profit']:+,.2f}  ({m['return_pct']:+.2f}%)  ← {profit}")
            print(f"  Max drawdown:  {m['max_drawdown_pct']:.2f}%")
            print(f"  Expectancy:    ${m['expectancy']:+,.2f}  |  MaxConsecLoss: {m['max_consecutive_losses']}")
            print(f"  Time:          {elapsed:.0f}s")
    except Exception as e:
        elapsed = time.time() - t0
        errors.append((full_label, str(e)))
        print(f"  ERROR ({elapsed:.0f}s): {e}")
        import traceback; traceback.print_exc()

# ─── SUMMARY TABLE ───────────────────────────────────────────────────────────
total_elapsed = time.time() - run_start

print(f"\n\n{'='*75}")
print(f"  MASTER RETEST SUMMARY")
print(f"{'='*75}")
print(f"  {'Scenario':<32} {'Trd':>4} {'Win%':>6} {'PF':>6} {'Net%':>8} {'DD%':>6} {'Result':>8}")
print(f"  {'─'*32} {'─'*4} {'─'*6} {'─'*6} {'─'*8} {'─'*6} {'─'*8}")

by_asset = {}
for asset, label, m in results:
    if asset not in by_asset:
        by_asset[asset] = []
    by_asset[asset].append((label, m))

asset_order = ["BTC", "ETH", "SOL", "BNB"]
grand_pnl       = 0.0
grand_trades    = 0
grand_profitable = 0
grand_total      = 0

for asset in asset_order:
    if asset not in by_asset:
        continue
    print(f"\n  ── {asset} ──")
    asset_pnl = 0.0
    asset_profitable = 0
    asset_total = 0

    for label, m in by_asset[asset]:
        short = label[:32]
        if m['total_trades'] == 0:
            print(f"  {short:<32} {'0':>4} {'—':>6} {'—':>6} {'—':>8} {'—':>6} {'NO TRD':>8}")
        else:
            flag = "✓ PROFIT" if m['net_profit'] > 0 else "✗ LOSS  "
            print(f"  {short:<32} {m['total_trades']:>4} {m['win_rate']*100:>5.1f}% "
                  f"{m['profit_factor']:>6.3f} {m['return_pct']:>+7.2f}% "
                  f"{m['max_drawdown_pct']:>5.2f}% {flag:>8}")
            asset_pnl += m['net_profit']
            asset_total += 1
            if m['net_profit'] > 0:
                asset_profitable += 1

    print(f"  {'':>32}  Asset total: ${asset_pnl:+,.2f}  |  {asset_profitable}/{asset_total} profitable")
    grand_pnl       += asset_pnl
    grand_trades    += sum(m['total_trades'] for _, m in by_asset[asset])
    grand_profitable += asset_profitable
    grand_total      += asset_total

print(f"\n{'='*75}")
print(f"  GRAND TOTAL:  ${grand_pnl:+,.2f}  |  {grand_profitable}/{grand_total} scenarios profitable")
print(f"  Total trades: {grand_trades}")
print(f"  Runtime:      {total_elapsed/60:.1f} min")
if errors:
    print(f"\n  ERRORS ({len(errors)}):")
    for lbl, err in errors:
        print(f"    [{lbl}]: {err}")
print(f"{'='*75}")
print("\nDone.")
