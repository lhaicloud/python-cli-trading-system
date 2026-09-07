"""One-off: compare exchange position/orders vs DB for an open live trade."""
from __future__ import annotations

import sqlite3
import sys

from app.config import get_settings
from app.live.exchange import BinanceExchangeClient

sym = (sys.argv[1] if len(sys.argv) > 1 else "LINKUSDT").upper()
cfg = get_settings()
c = BinanceExchangeClient(
    api_key=cfg.binance_api_key,
    api_secret=cfg.binance_api_secret,
    testnet=cfg.binance_testnet,
)
pos = c.get_position(sym)
mark = c.get_mark_price(sym)
orders = c.get_open_orders(sym)
algos = c.get_open_algo_orders(sym)

conn = sqlite3.connect("data/db/lqmtf.db")
conn.row_factory = sqlite3.Row
row = conn.execute(
    """
    SELECT id, symbol, direction, status, entry_price, stop_loss, take_profit,
           position_size, exchange_sl_id, exchange_tp_id, partial_tp_price
    FROM live_trades WHERE symbol=? AND status='open' ORDER BY id DESC LIMIT 1
    """,
    (sym,),
).fetchone()

print("=== EXCHANGE ===")
print(f"mark_price: {mark}")
print(f"position: {dict(pos) if pos else None}")
print(f"open_orders: {len(orders)}")
for o in orders:
    print(
        f"  {o.get('type')} {o.get('side')} stop={o.get('stopPrice')} "
        f"price={o.get('price')} id={o.get('orderId')}"
    )
print(f"algo_orders: {len(algos)}")
for a in algos:
    print(
        f"  {a.get('orderType')} {a.get('side')} trigger={a.get('triggerPrice')} "
        f"algoId={a.get('algoId')} status={a.get('algoStatus')}"
    )

print("=== DB live_trades (open) ===")
print(dict(row) if row else "none")
