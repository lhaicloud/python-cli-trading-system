import sys, sqlite3, requests
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from datetime import datetime, timezone

def get_price(sym):
    r = requests.get(f'https://api.binance.com/api/v3/ticker/price?symbol={sym}', timeout=5)
    return float(r.json()['price'])

conn = sqlite3.connect('data/db/lqmtf.db')
conn.row_factory = sqlite3.Row
trades = conn.execute("SELECT * FROM paper_trades WHERE status='open' ORDER BY id").fetchall()

if not trades:
    print("No open trades.")
    conn.close()
    sys.exit(0)

now_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
total_pnl = 0.0

print(f"Closing all open trades at market — {now_utc}\n")

for t in trades:
    price = get_price(t['symbol'])
    size  = t['position_size']
    cap   = t['capital_at_risk']

    if t['direction'] == 'BUY':
        pnl = (price - t['entry_price']) * size
    else:
        pnl = (t['entry_price'] - price) * size

    pnl_pct = pnl / cap * 100
    total_pnl += pnl

    conn.execute("""
        UPDATE paper_trades SET
            status      = 'closed',
            close_price = ?,
            close_time  = ?,
            pnl         = ?,
            pnl_pct     = ?,
            updated_at  = datetime('now')
        WHERE id = ?
    """, (price, now_utc, round(pnl, 2), round(pnl_pct, 4), t['id']))

    sign = '+' if pnl >= 0 else ''
    print(f"  #{t['id']} {t['symbol']} {t['direction']}  entry={t['entry_price']}  close={price:.4f}  PnL={sign}${pnl:.2f} ({sign}{pnl_pct:.2f}%)")

conn.commit()
conn.close()

sign = '+' if total_pnl >= 0 else ''
print(f"\n  All {len(trades)} trades closed.")
print(f"  Net realized P&L: {sign}${total_pnl:.2f}")
