"""
A/B Test: ML=ON vs ML=OFF on recent periods.
Runs each window twice (with/without ML) using subprocesses so env vars take effect.
"""
import subprocess
import sys
import json
import os

CAPITAL = 10_000.0
PYTHON  = sys.executable

AB_WINDOWS = {
    # BNB: zero 2025 data in training -- entire 2025 is truly unseen
    "BNBUSDT": [
        ("BNB Jan-Mar 2025", "2025-01-01", "2025-03-31"),
        ("BNB Apr-Jun 2025", "2025-04-01", "2025-06-30"),
        ("BNB Jul-Sep 2025", "2025-07-01", "2025-09-30"),
    ],
    # SOL: training ends Jun 2025 -- Jul-Dec 2025 is unseen
    "SOLUSDT": [
        ("SOL Jul-Sep 2025", "2025-07-01", "2025-09-30"),
        ("SOL Oct-Dec 2025", "2025-10-01", "2025-12-31"),
    ],
    # ETH: sparse 2026, Apr+ is unseen
    "ETHUSDT": [
        ("ETH Apr-May 2026", "2026-04-01", "2026-05-22"),
    ],
    # BTC: training goes through Apr 2026, use Feb-Apr 2026 as test
    "BTCUSDT": [
        ("BTC Feb-Apr 2026", "2026-02-01", "2026-04-30"),
    ],
}


def run_backtest(symbol, s, e, ml_on):
    env = os.environ.copy()
    if not ml_on:
        env["DISABLE_ML_MODEL"] = "1"
    else:
        env.pop("DISABLE_ML_MODEL", None)
    proc = subprocess.run(
        [PYTHON, "run_single_backtest.py", symbol, s, e, str(CAPITAL)],
        capture_output=True, text=True, env=env, timeout=600,
        cwd=os.path.dirname(os.path.abspath(__file__)),
    )
    # Last line of stdout should be the JSON metrics
    lines = [l.strip() for l in proc.stdout.strip().splitlines() if l.strip().startswith("{")]
    if lines:
        return json.loads(lines[-1])
    raise RuntimeError(proc.stderr[-500:] if proc.stderr else "no output")


print("=" * 62)
print("  A/B TEST: ML=ON vs ML=OFF")
print("=" * 62)

summary = []
for symbol, windows in AB_WINDOWS.items():
    print(f"\n{symbol}")
    print(f"  {'Period':<22}  {'ML':>3}  {'Trades':>6}  {'WR':>5}  {'P&L':>10}")
    print(f"  {'-'*22}  {'-'*3}  {'-'*6}  {'-'*5}  {'-'*10}")

    for label, s, e in windows:
        for ml_on in [False, True]:
            try:
                m = run_backtest(symbol, s, e, ml_on)
                flag = "ON " if ml_on else "OFF"
                sign = "+" if m["net_profit"] >= 0 else ""
                print(f"  {label:<22}  {flag}  {m['total_trades']:>6}  {m['win_rate']:.0%}  {sign}${m['net_profit']:>8,.0f}")
                summary.append({"symbol": symbol, "label": label, "ml": ml_on, **m})
            except Exception as ex:
                flag = "ON " if ml_on else "OFF"
                print(f"  {label:<22}  {flag}  ERROR: {ex}")

print("\n" + "=" * 62)
print("  SUMMARY: ML edge per window")
print("=" * 62)

grouped = {}
for r in summary:
    k = (r["symbol"], r["label"])
    grouped.setdefault(k, {})[r["ml"]] = r

for (sym, lbl), d in grouped.items():
    if True in d and False in d:
        dp   = d[True]["net_profit"] - d[False]["net_profit"]
        dwr  = d[True]["win_rate"]   - d[False]["win_rate"]
        dtrades = d[True]["total_trades"] - d[False]["total_trades"]
        verdict = "BETTER" if dp > 0 else "WORSE "
        print(f"  {sym} {lbl}: ML {verdict}  dP&L={dp:+,.0f}  dWR={dwr:+.0%}  dTrades={dtrades:+d}")

ml_total   = sum(d[True]["net_profit"]  for d in grouped.values() if True  in d)
noml_total = sum(d[False]["net_profit"] for d in grouped.values() if False in d)
print(f"\n  ALL WINDOWS  ML=ON:  ${ml_total:,.0f}")
print(f"  ALL WINDOWS  ML=OFF: ${noml_total:,.0f}")
print(f"  ML total edge:       ${ml_total - noml_total:+,.0f}")
print("\nDone.")
