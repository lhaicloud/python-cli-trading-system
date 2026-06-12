import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from app.data.repository import get_candles
from app.ta.signals import generate_signal
from app.ta.indicators import add_indicators

df_1d  = get_candles('BTCUSDT', '1d')
df_12h = get_candles('BTCUSDT', '12h')
df_4h  = get_candles('BTCUSDT', '4h')
df_1h  = get_candles('BTCUSDT', '1h')
df_30m = get_candles('BTCUSDT', '30m')
df_1w  = get_candles('BTCUSDT', '1w')

sig = generate_signal('BTCUSDT', df_1d, df_4h, df_1h, df_30m,
                      df_12h=df_12h, df_1w=df_1w)
print(f"Signal:   {sig.signal}")
print(f"Conf:     {sig.confidence}")
print(f"Regime:   {sig.market_regime}")
print(f"Model:    {sig.model_version}")
print("Integration OK")
