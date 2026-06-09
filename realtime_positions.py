import sys, sqlite3, requests, time, os
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

REFRESH = 3  # seconds

def clear():
    os.system('cls' if os.name == 'nt' else 'clear')

def get_open_trades():
    conn = sqlite3.connect('data/db/lqmtf.db')
    conn.row_factory = sqlite3.Row
    trades = conn.execute(
        "SELECT * FROM paper_trades WHERE status='open' ORDER BY id"
    ).fetchall()
    conn.close()
    return trades

def get_price(sym):
    try:
        r = requests.get(
            f'https://api.binance.com/api/v3/ticker/price?symbol={sym}',
            timeout=3
        )
        return float(r.json()['price'])
    except Exception:
        return None

def render(trades):
    clear()
    now_ts = time.strftime('%Y-%m-%d %H:%M:%S')
    print(f"  Live Paper Positions — {now_ts}  (refreshes every {REFRESH}s, Ctrl+C to stop)")
    print("=" * 100)

    if not trades:
        print("  No open trades.")
        print("=" * 100)
        return

    header = f"  {'#':<4} {'Symbol':<10} {'Dir':<5} {'Entry':>11} {'Price':>11} {'TP':>11} {'SL':>11} {'Unreal $':>10} {'%':>7} {'->TP':>9} {'->SL':>9} Status"
    print(header)
    print("  " + "-" * 96)

    total = 0.0
    for t in trades:
        sym   = t['symbol']
        tid   = t['id']
        dirn  = t['direction']
        entry = t['entry_price']
        sl    = t['stop_loss']
        tp    = t['take_profit']
        size  = t['position_size']
        cap   = t['capital_at_risk']
        opened = t['open_time']

        price = get_price(sym)
        if price is None:
            print(f"  #{tid:<3} {sym:<10} {dirn:<5}  -- price fetch failed --")
            continue

        if dirn == 'BUY':
            unreal  = (price - entry) * size
            dist_tp = tp - price
            dist_sl = price - sl
            hit_tp  = price >= tp
            hit_sl  = price <= sl
        else:
            unreal  = (entry - price) * size
            dist_tp = price - tp
            dist_sl = sl - price
            hit_tp  = price <= tp
            hit_sl  = price >= sl

        pct    = unreal / cap * 100
        total += unreal

        if hit_tp:
            status = '*** TP HIT ***'
        elif hit_sl:
            status = '*** SL HIT ***'
        elif unreal > 0:
            bar = '#' * min(int(abs(dist_tp) / (abs(tp - entry)) * 20), 20)
            status = f'profit  [{bar:<20}]'
        else:
            status = 'loss'

        sign = '+' if unreal >= 0 else ''
        print(f"  #{tid:<3} {sym:<10} {dirn:<5} {entry:>11.4f} {price:>11.4f} {tp:>11.4f} {sl:>11.4f} "
              f"{sign}${unreal:>8.2f} {sign}{pct:>5.1f}%  "
              f"{dist_tp:>+9.4f}  {dist_sl:>+9.4f}  {status}")
        print(f"       opened: {opened}")
        print()

    print("  " + "-" * 96)
    sign = '+' if total >= 0 else ''
    print(f"  Total unrealized P&L:  {sign}${total:.2f}")
    print("=" * 100)

print("Starting realtime position monitor... press Ctrl+C to stop.")
time.sleep(1)

try:
    while True:
        trades = get_open_trades()
        render(trades)
        time.sleep(REFRESH)
except KeyboardInterrupt:
    print("\nStopped.")
