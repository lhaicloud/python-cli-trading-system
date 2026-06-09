"""
BTC Regime-Labeled Scenario Backtest — 15 distinct market personalities.
Each scenario is a real, named market event with its regime type.
Runs WITH ML enabled. Covers crashes, mega-bulls, chop, recoveries, black swans.
"""
import sys, io, os, datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from app.backtesting.engine import run_backtest

# (label, regime_type, start, end)
SCENARIOS = [
    # --- CRASHES & PANIC ---
    ("COVID CRASH",          "BLACK_SWAN",    "2020-02-15", "2020-03-31"),  # BTC $10k->$4k in days
    ("CHINA BAN CRASH",      "SHARP_DECLINE", "2021-05-10", "2021-07-20"),  # BTC $58k->$30k
    ("LUNA/3AC IMPLOSION",   "CATASTROPHIC",  "2022-05-01", "2022-07-31"),  # BTC $38k->$18k
    ("FTX COLLAPSE",         "SHARP_DECLINE", "2022-10-15", "2022-12-31"),  # SBF fraud, -35%

    # --- MEGA BULLS ---
    ("2020 HALVING BULL",    "MEGA_BULL",     "2020-10-01", "2021-01-08"),  # $11k->$40k
    ("2021 Q1 MEGA BULL",    "MEGA_BULL",     "2021-01-08", "2021-04-15"),  # $40k->$64k
    ("2024 ETF PUMP",        "MEGA_BULL",     "2024-01-01", "2024-03-31"),  # $42k->$72k
    ("2024 TRUMP PUMP",      "MEGA_BULL",     "2024-10-01", "2024-12-31"),  # $60k->$100k+

    # --- RECOVERIES ---
    ("COVID RECOVERY",       "RECOVERY",      "2020-04-01", "2020-07-31"),  # slow grind up
    ("POST-CRASH RECOVERY",  "RECOVERY",      "2022-08-01", "2022-10-14"),  # dead-cat + chop
    ("2023 RECOVERY",        "RECOVERY",      "2023-01-01", "2023-04-30"),  # $16k->$30k

    # --- CHOP / CONSOLIDATION ---
    ("ATH CHOP PRE-TOP",     "CHOP",          "2021-07-20", "2021-10-01"),  # $30k-$48k sideways
    ("2023 CONSOLIDATION",   "CHOP",          "2023-05-01", "2023-10-31"),  # $25k-$30k 6 months
    ("POST-HALVING CHOP",    "CHOP",          "2024-04-20", "2024-07-31"),  # after halving cool-off

    # --- BLOW-OFF TOPS ---
    ("2021 ATH BLOW-OFF",    "BLOW_OFF_TOP",  "2021-10-01", "2021-11-10"),  # $48k->$69k ATH
]

CAPITAL  = 10_000.0
RISK_PCT = 1.0
SYMBOL   = "BTCUSDT"

# Group by regime for summary
regime_results: dict[str, list] = {}

print(f"\n{'='*70}")
print(f"  BTC REGIME SCENARIOS  ({len(SCENARIOS)} events, ML=ON)")
print(f"{'='*70}")

all_results = []
for label, regime, start_str, end_str in SCENARIOS:
    start_ms = int(datetime.datetime.strptime(start_str, "%Y-%m-%d")
                   .replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
    end_ms   = int(datetime.datetime.strptime(end_str, "%Y-%m-%d")
                   .replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)

    print(f"\n  [{regime}]  {label}  ({start_str} to {end_str})")
    print(f"  {'-'*60}")
    try:
        res = run_backtest(SYMBOL, start_ms, end_ms, CAPITAL, RISK_PCT)
        m   = res["metrics"]
        t   = res["trades"]
        wins   = sum(1 for x in t if x["pnl"] > 0)
        losses = sum(1 for x in t if x["pnl"] <= 0)
        net    = m["net_profit"]
        pct    = net / CAPITAL * 100
        wr     = m["win_rate"]
        pf     = m.get("profit_factor", 0)
        tc     = m["total_trades"]
        dd     = m.get("max_drawdown_pct", 0)
        flag   = "PROFIT" if net > 0 else "LOSS"
        sign   = "+" if net >= 0 else ""
        print(f"  Trades: {tc}  ({wins}W/{losses}L)  WR={wr:.1%}  PF={pf:.2f}  DD={dd:.1f}%")
        print(f"  Net: {sign}${net:,.0f}  ({sign}{pct:.1f}%)  [{flag}]")
        all_results.append((label, regime, net, wr, pf, tc, dd))
        regime_results.setdefault(regime, []).append((label, net))
    except Exception as e:
        print(f"  ERROR: {e}")
        all_results.append((label, regime, None, None, None, None, None))
        regime_results.setdefault(regime, []).append((label, None))

# ── Summary by regime ──────────────────────────────────────────────────────
print(f"\n{'='*70}")
print(f"  RESULTS BY REGIME")
print(f"{'='*70}")
regime_order = ["MEGA_BULL", "RECOVERY", "CHOP", "SHARP_DECLINE", "CATASTROPHIC", "BLACK_SWAN", "BLOW_OFF_TOP"]
for rtype in regime_order:
    if rtype not in regime_results:
        continue
    items = regime_results[rtype]
    valid = [(l, n) for l, n in items if n is not None]
    if not valid:
        continue
    total  = sum(n for _, n in valid)
    wins_r = sum(1 for _, n in valid if n > 0)
    sign   = "+" if total >= 0 else ""
    print(f"\n  {rtype}  ({wins_r}/{len(valid)} profitable)  Total={sign}${total:,.0f}")
    for lbl, net in valid:
        s = "+" if net >= 0 else ""
        flag = "[+]" if net >= 0 else "[-]"
        print(f"    {lbl:30s}: {s}${net:,.0f}  {flag}")

# ── Overall summary ────────────────────────────────────────────────────────
valid_all = [(l, r, n, wr, pf, tc, dd) for l, r, n, wr, pf, tc, dd in all_results if n is not None]
total_net  = sum(x[2] for x in valid_all)
profitable = sum(1 for x in valid_all if x[2] > 0)
avg_wr     = sum(x[3] for x in valid_all) / len(valid_all) if valid_all else 0
avg_pf     = sum(x[4] for x in valid_all) / len(valid_all) if valid_all else 0

print(f"\n{'='*70}")
print(f"  OVERALL: {profitable}/{len(valid_all)} scenarios profitable")
print(f"  Avg WR={avg_wr:.1%}  Avg PF={avg_pf:.2f}  Total net per $10k: +${total_net:,.0f}")
print(f"{'='*70}")
print("Done.")
