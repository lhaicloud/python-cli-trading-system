import urllib.request, json, datetime

r = urllib.request.urlopen('https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT', timeout=5)
price = float(json.loads(r.read())['price'])

entry = 77905.98
sl    = 78435.0011
tp    = 76652.641
size  = 0.189028

open_time = datetime.datetime(2026, 5, 21, 19, 36, tzinfo=datetime.timezone.utc)
now = datetime.datetime.now(datetime.timezone.utc)
duration = now - open_time

pnl_usd = (entry - price) * size
pnl_pct = (entry - price) / entry * 100

print(f"=== OPEN PAPER TRADE STATUS ===")
print(f"BTC Current Price: ${price:,.2f}")
print(f"")
print(f"Trade:   SELL  |  Opened: {open_time.strftime('%Y-%m-%d %H:%M UTC')} ({duration})")
print(f"Entry:   ${entry:,.2f}")
print(f"SL:      ${sl:,.2f}  (+${sl-price:,.2f} to invalidate)")
print(f"TP:      ${tp:,.2f}  (-${price-tp:,.2f} to target)")
print(f"Size:    {size} BTC  |  Risk: $100")
print(f"")
if price < entry:
    pct_to_tp = (entry - price) / (entry - tp) * 100
    print(f"Status:  IN PROFIT  ({pct_to_tp:.1f}% of the way to TP)")
    print(f"P&L:     +${pnl_usd:,.2f}  ({pnl_pct:.2f}%)")
else:
    pct_to_sl = (price - entry) / (sl - entry) * 100
    print(f"Status:  IN LOSS  ({pct_to_sl:.1f}% of the way to SL)")
    print(f"P&L:     ${pnl_usd:,.2f}  ({pnl_pct:.2f}%)")
print(f"")
print(f"Note: DB status='open', new watcher instance did NOT resume tracking this trade.")
