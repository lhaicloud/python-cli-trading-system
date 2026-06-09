"""Close all open paper trading positions at current market price."""
import sys, io, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from dotenv import load_dotenv; load_dotenv()
from app.db.migrations import run_migrations; run_migrations()
from app.data.repository import get_open_paper_trades, close_paper_trade
import httpx

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
now_ms = int(time.time() * 1000)

total_closed = 0
total_pnl = 0.0

for sym in SYMBOLS:
    trades = get_open_paper_trades(sym)
    if not trades:
        print(f"{sym}: no open positions")
        continue
    resp = httpx.get(f"https://api.binance.com/api/v3/ticker/price?symbol={sym}")
    price = float(resp.json()["price"])
    for t in trades:
        if t["direction"] == "BUY":
            pnl = (price - t["entry_price"]) * t["position_size"]
        else:
            pnl = (t["entry_price"] - price) * t["position_size"]
        pnl_pct = pnl / (t["entry_price"] * t["position_size"]) * 100
        close_paper_trade(t["id"], price, now_ms, round(pnl, 2), round(pnl_pct, 4), "closed_manual")
        total_pnl += pnl
        total_closed += 1
        print(f"  CLOSED  {sym} {t['direction']:4s} | entry=${t['entry_price']:,.4f}  now=${price:,.4f}  PnL=${pnl:+,.2f}")

print()
if total_closed == 0:
    print("No open positions found across all symbols.")
else:
    print(f"Closed {total_closed} position(s)  |  Total PnL: ${total_pnl:+,.2f}")
