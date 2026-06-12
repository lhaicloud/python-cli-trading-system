"""
A/B Test: ML=ON vs ML=OFF — runs directly in-process (no subprocesses).
Avoids subprocess timeout issues. Uses importlib to reload buy/sell model modules.
"""
import os
import json
import importlib
import datetime

CAPITAL = 10_000.0

AB_WINDOWS = {
    "BNBUSDT": [
        ("BNB Jan-Mar 2025", "2025-01-01", "2025-03-31"),
        ("BNB Apr-Jun 2025", "2025-04-01", "2025-06-30"),
        ("BNB Jul-Sep 2025", "2025-07-01", "2025-09-30"),
    ],
    "SOLUSDT": [
        ("SOL Jul-Sep 2025", "2025-07-01", "2025-09-30"),
        ("SOL Oct-Dec 2025", "2025-10-01", "2025-12-31"),
    ],
    "ETHUSDT": [
        ("ETH Apr-May 2026", "2026-04-01", "2026-05-22"),
    ],
    "BTCUSDT": [
        ("BTC Feb-Apr 2026", "2026-02-01", "2026-04-30"),
    ],
}


def to_ms(s):
    return int(datetime.datetime.strptime(s, "%Y-%m-%d")
               .replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)


def run_one(symbol, label, s, e, ml_on):
    """Run a single backtest with ML on or off. Reloads signals module to respect flag."""
    flag = "ON " if ml_on else "OFF"
    os.environ["DISABLE_ML_MODEL"] = "0" if ml_on else "1"

    # Reload modules that read the env var at import time
    import app.ta.signals as sig_mod
    sig_mod._USE_ML_MODEL = ml_on
    # Also invalidate model caches so they reload cleanly
    try:
        from app.models.buy_model import invalidate_cache as bc
        from app.models.sell_model import invalidate_cache as sc
        bc(); sc()
    except Exception:
        pass

    from app.backtesting.engine import run_backtest
    start_ms = to_ms(s)
    end_ms   = to_ms(e)
    try:
        result = run_backtest(symbol, start_ms, end_ms, CAPITAL, 1.0)
        m = result["metrics"]
        sign = "+" if m["net_profit"] >= 0 else ""
        print(f"  {label:<22}  {flag}  {m['total_trades']:>6}  {m['win_rate']:.0%}  {sign}${m['net_profit']:>8,.0f}")
        return m
    except Exception as ex:
        print(f"  {label:<22}  {flag}  ERROR: {ex}")
        return None


print("=" * 62)
print("  A/B TEST: ML=ON vs ML=OFF (direct, no subprocesses)")
print("=" * 62)

summary = []
for symbol, windows in AB_WINDOWS.items():
    print(f"\n{symbol}")
    print(f"  {'Period':<22}  {'ML':>3}  {'Trades':>6}  {'WR':>5}  {'P&L':>10}")
    print(f"  {'-'*22}  {'-'*3}  {'-'*6}  {'-'*5}  {'-'*10}")
    for label, s, e in windows:
        for ml_on in [False, True]:
            m = run_one(symbol, label, s, e, ml_on)
            if m:
                summary.append({"symbol": symbol, "label": label, "ml": ml_on, **m})

print("\n" + "=" * 62)
print("  SUMMARY: ML edge per window")
print("=" * 62)

grouped = {}
for r in summary:
    k = (r["symbol"], r["label"])
    grouped.setdefault(k, {})[r["ml"]] = r

ml_wins = 0
ml_losses = 0
for (sym, lbl), d in grouped.items():
    if True in d and False in d:
        dp   = d[True]["net_profit"] - d[False]["net_profit"]
        dwr  = d[True]["win_rate"]   - d[False]["win_rate"]
        dtrades = d[True]["total_trades"] - d[False]["total_trades"]
        verdict = "BETTER" if dp > 0 else "WORSE "
        if dp > 0:
            ml_wins += 1
        else:
            ml_losses += 1
        print(f"  {sym} {lbl}: ML {verdict}  dP&L={dp:+,.0f}  dWR={dwr:+.0%}  dTrades={dtrades:+d}")

ml_total   = sum(d[True]["net_profit"]  for d in grouped.values() if True  in d)
noml_total = sum(d[False]["net_profit"] for d in grouped.values() if False in d)
print(f"\n  ALL WINDOWS  ML=ON:  ${ml_total:,.0f}")
print(f"  ALL WINDOWS  ML=OFF: ${noml_total:,.0f}")
print(f"  ML total edge:       ${ml_total - noml_total:+,.0f}")
print(f"  Windows where ML BETTER: {ml_wins}/{ml_wins+ml_losses}")
print("\nDone.")
