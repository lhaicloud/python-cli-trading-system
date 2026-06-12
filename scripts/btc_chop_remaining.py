import sys, io, datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
from app.backtesting.engine import run_backtest

SCENARIOS = [
    # ATH CHOP PRE-TOP skipped — deadlocks backtest engine (Jul-Oct 2021 data issue)
    ("2023 CONSOLIDATION", "CHOP",         "2023-05-01", "2023-10-31"),
    ("POST-HALVING CHOP",  "CHOP",         "2024-04-20", "2024-07-31"),
    ("2021 ATH BLOW-OFF",  "BLOW_OFF_TOP", "2021-10-01", "2021-11-10"),
]
CAPITAL, SYMBOL = 10_000.0, "BTCUSDT"

for label, regime, start_str, end_str in SCENARIOS:
    start_ms = int(datetime.datetime.strptime(start_str, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
    end_ms   = int(datetime.datetime.strptime(end_str,   "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
    print(f"\n  [{regime}]  {label}  ({start_str} to {end_str})", flush=True)
    try:
        res = run_backtest(SYMBOL, start_ms, end_ms, CAPITAL, 1.0)
        m = res["metrics"]; t = res["trades"]
        wins   = sum(1 for x in t if x["pnl"] > 0)
        losses = sum(1 for x in t if x["pnl"] <= 0)
        net    = m["net_profit"]
        flag   = "PROFIT" if net > 0 else "LOSS"
        sign   = "+" if net >= 0 else ""
        print(f"  Trades: {m['total_trades']}  ({wins}W/{losses}L)  WR={m['win_rate']:.1%}  PF={m.get('profit_factor',0):.2f}  DD={m.get('max_drawdown_pct',0):.1f}%", flush=True)
        print(f"  Net: {sign}${net:,.0f}  ({sign}{net/CAPITAL*100:.1f}%)  [{flag}]", flush=True)
    except Exception as e:
        print(f"  ERROR: {e}", flush=True)

print("\nDone.", flush=True)
