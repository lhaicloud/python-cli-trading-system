"""Quick test: verify model integration works end-to-end without errors."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from app.models.buy_model import score_buy_setup
from app.models.sell_model import score_sell_setup

# Test 1: Model loading
print("Test 1: model loading...")
features = {
    "h4_bias_score": 2.0, "h1_conf_score": 1.0, "zone_score": 72.0,
    "liq_sweep": 1, "pd_location_score": 1.0, "rr_ratio": 2.5,
    "volume_ratio": 1.8, "atr_ratio": 1.2, "rsi_14": 35.0,
    "body_atr_ratio": 1.1, "above_ema50": 0, "above_ema200": 0,
    "regime_score": 0.0,
}
buy_prob = score_buy_setup("BTCUSDT", features)
sell_prob = score_sell_setup("BTCUSDT", features)
print(f"  BUY prob  = {buy_prob:.4f}")
print(f"  SELL prob = {sell_prob:.4f}")

# Test 2: Second call uses cache (fast)
print("Test 2: cache hit...")
buy_prob2 = score_buy_setup("BTCUSDT", features)
assert buy_prob2 == buy_prob, "Cache returned different result"
print(f"  Cache OK, same result: {buy_prob2:.4f}")

# Test 3: Signal generation with model
print("Test 3: full signal generation...")
from app.data.repository import get_candles
from app.ta.signals import generate_signal

df_1d  = get_candles('BTCUSDT', '1d')
df_4h  = get_candles('BTCUSDT', '4h')
df_1h  = get_candles('BTCUSDT', '1h')
df_30m = get_candles('BTCUSDT', '30m')

sig = generate_signal('BTCUSDT', df_1d, df_4h, df_1h, df_30m)
print(f"  Signal: {sig.signal}  Conf: {sig.confidence}  Regime: {sig.market_regime}")

print("\nAll tests passed.")
