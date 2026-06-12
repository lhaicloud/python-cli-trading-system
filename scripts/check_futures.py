from dotenv import load_dotenv; load_dotenv()
from app.config import get_settings
cfg = get_settings()
print(f"Base URL:  {cfg.binance_base_url}")
print(f"Fee pct:   {cfg.backtest_fee_pct}%")
from app.data.binance_client import BinanceClient
c = BinanceClient()
t = c.get_server_time()
p = c.get_ticker_price("BTCUSDT")
c.close()
print(f"Server time OK: {t}")
print(f"BTC futures price: ${p:,.2f}")
print("FUTURES API: OK")
