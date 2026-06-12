import sys, sqlite3
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# Trade #3: SOLUSDT SELL entry=86.7466, tp=85.3684, position_size=140.864911
entry = 86.7466
tp    = 85.3684
size  = 140.864911
pnl   = (entry - tp) * size        # SELL: profit when price falls to TP
pnl_pct = (entry - tp) / entry * 100
mfe   = (entry - tp) * size        # max favorable = reached TP exactly

print(f"Closing Trade #3 SOLUSDT SELL as TP_HIT")
print(f"  Entry:    {entry}")
print(f"  TP:       {tp}")
print(f"  PnL:      ${pnl:+.2f}")
print(f"  PnL%:     {pnl_pct:+.3f}%")

conn = sqlite3.connect('data/db/lqmtf.db')
conn.execute("""
    UPDATE paper_trades SET
        status = 'closed',
        close_price = ?,
        close_time = '2026-05-24 13:30 UTC',
        pnl = ?,
        pnl_pct = ?,
        max_favorable_excursion = ?,
        updated_at = datetime('now')
    WHERE id = 3
""", (tp, round(pnl, 2), round(pnl_pct, 4), round(mfe, 2)))
conn.commit()
conn.close()
print("Done — Trade #3 marked closed (tp_hit).")
