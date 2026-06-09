import sys, io, warnings
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
warnings.filterwarnings('ignore')
from dotenv import load_dotenv; load_dotenv()
from app.db.migrations import run_migrations; run_migrations()
from app.backtesting.engine import run_backtest
from app.utils.timeframes import str_to_ms

SYMBOL = "ARBUSDT"
CAPITAL = 10_000.0
RISK    = 1.0

# ARB listed March 2023 — scenarios from listing date onwards
scenarios = [
    ("ARB LAUNCH    Q2-2023", "2023-04-01", "2023-06-30"),
    ("ARB RECOVERY  Q3-2023", "2023-07-01", "2023-09-30"),
    ("ARB BREAKOUT  Q1-2024", "2024-01-01", "2024-03-31"),
    ("ARB SUMMER    Q2-2024", "2024-04-01", "2024-06-30"),
    ("ARB PULLBACK  Q3-2024", "2024-07-01", "2024-09-30"),
    ("ARB RECENT    Q1-2025", "2025-01-01", "2025-03-31"),
]

print(f"\nARBUSDT CROSS-ASSET VALIDATION  ({len(scenarios)} scenarios)\n")
results = []
for label, start, end in scenarios:
    print(f"{label}  [{start} to {end}]")
    r = run_backtest(SYMBOL, str_to_ms(start), str_to_ms(end), CAPITAL, RISK)
    m = r["metrics"]
    t = m.get("total_trades", 0)
    w = m.get("win_rate", 0)
    p = m.get("net_profit", 0)
    f = m.get("profit_factor", 0)
    if t == 0:
        print("No trades generated\n"); results.append((label, 0, 0, 0, 0)); continue
    print(f"Net profit:      ${p:,.2f}  ({p/CAPITAL*100:.2f}%)")
    print(f"Win rate:        {w*100:.1f}%  ({int(w*t)}W / {t-int(w*t)}L)")
    print(f"Profit factor:   {f:.3f}\n")
    results.append((label, t, w, p, f))

print("\nARBUSDT SUMMARY")
total = 0
prof  = 0
for label, t, w, p, f in results:
    if t == 0:
        print(f"  {label:<28}: no trades  [SKIP]")
    else:
        tag = "[PROFIT]" if p > 0 else "[LOSS]"
        print(f"  {label:<28}: +${p:>8,.0f}  ({w*100:.1f}% WR, PF={f:.2f}, {t}t)  {tag}")
        total += p
        if p > 0: prof += 1
print(f"\n{prof}/{sum(1 for _,t,_,_,_ in results if t>0)} profitable  |  Total: ${total:+,.2f}")
