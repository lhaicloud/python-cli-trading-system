import sys, sqlite3, requests
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

conn = sqlite3.connect('data/db/lqmtf.db')
conn.row_factory = sqlite3.Row
trades = conn.execute(
    "SELECT * FROM paper_trades WHERE status='open' ORDER BY id"
).fetchall()
conn.close()

if not trades:
    print("No open trades.")
    sys.exit(0)

print(f"{'#':<4} {'Symbol':<10} {'Dir':<5} {'Entry':>10} {'Now':>10} {'SL':>10} {'TP':>10} {'Unreal P&L':>11} {'Dist TP':>9} {'Dist SL':>9} {'Status'}")
print("-" * 110)

total_pnl = 0.0
for t in trades:
    sym    = t['symbol']
    tid    = t['id']
    dirn   = t['direction']
    entry  = t['entry_price']
    sl     = t['stop_loss']
    tp     = t['take_profit']
    size   = t['position_size']
    cap    = t['capital_at_risk']

    r = requests.get(f'https://api.binance.com/api/v3/ticker/price?symbol={sym}')
    now = float(r.json()['price'])

    if dirn == 'BUY':
        unreal = (now - entry) * size
        dist_tp = tp - now
        dist_sl = now - sl
        hit_tp = now >= tp
        hit_sl = now <= sl
    else:
        unreal = (entry - now) * size
        dist_tp = now - tp
        dist_sl = sl - now
        hit_tp = now <= tp
        hit_sl = now >= sl

    pnl_pct = unreal / cap * 100
    status = 'TP HIT' if hit_tp else ('SL HIT' if hit_sl else ('in profit' if unreal > 0 else 'in loss'))
    total_pnl += unreal

    print(f"#{tid:<3} {sym:<10} {dirn:<5} {entry:>10.4f} {now:>10.4f} {sl:>10.4f} {tp:>10.4f} "
          f"${unreal:>+9.2f} ({pnl_pct:+.2f}%)  "
          f"{'+'if dist_tp>0 else ''}{dist_tp:.4f}  {'+'if dist_sl>0 else ''}{dist_sl:.4f}  {status}")

print("-" * 110)
print(f"{'Total unrealized P&L:':<60} ${total_pnl:>+9.2f}")
