import sys, io, warnings
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
warnings.filterwarnings('ignore')
from dotenv import load_dotenv; load_dotenv()
from app.db.migrations import run_migrations; run_migrations()
from app.backtesting.engine import run_backtest
from app.utils.timeframes import str_to_ms

SYMBOL = "DOGEUSDT"
CAPITAL = 10_000.0
RISK    = 1.0

scenarios = [
    ("DOGE MEGA-BULL Q1-2021", "2021-01-01", "2021-03-31"),
    ("DOGE ATH-CRASH Q4-2021", "2021-10-01", "2021-12-31"),
    ("DOGE BEAR      Q2-2022", "2022-04-01", "2022-06-30"),
    ("DOGE RECOVERY  Q1-2023", "2023-01-01", "2023-03-31"),
    ("DOGE BREAKOUT  Q1-2024", "2024-01-01", "2024-03-31"),
    ("DOGE PULLBACK  Q3-2024", "2024-07-01", "2024-09-30"),
]

print(f"\nDOGEUSDT CROSS-ASSET VALIDATION  ({len(scenarios)} scenarios)\n")
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

print("\nDOGEUSDT SUMMARY")
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
