from app.db.migrations import run_migrations
run_migrations()
from app.data.repository import get_open_paper_trades

symbols = ["OPUSDT","ADAUSDT","ARBUSDT","NEARUSDT","APTUSDT","BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT"]
found = False
for sym in symbols:
    trades = get_open_paper_trades(sym)
    for t in trades:
        print(f"{sym}: dir={t.get('direction')} entry={t.get('entry_price')} sl={t.get('stop_loss')} tp={t.get('take_profit')} size={t.get('position_size')} opened={t.get('opened_at')}")
        found = True
if not found:
    print("No open positions")
