"""Diagnose why the strategy generates so few signals."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from dotenv import load_dotenv; load_dotenv()
from app.db.migrations import run_migrations; run_migrations()

import pandas as pd
from app.data.repository import get_candles
from app.ta.indicators import add_indicators
from app.ta.signals import generate_signal
from app.ta.zones import detect_zones, price_in_zone
from app.ta.regime import classify_regime
from app.ta.trend import detect_trend_bias
from app.ta.zone_scoring import score_zone
from app.utils.timeframes import str_to_ms

# Load a 3-month window from 2023 (bull market)
start_ms = str_to_ms("2023-01-01")
end_ms   = str_to_ms("2023-03-31")

df_30m = get_candles("BTCUSDT", "30m", start_ms=start_ms, end_ms=end_ms)
df_1h  = get_candles("BTCUSDT", "1h",  start_ms=start_ms, end_ms=end_ms)
df_4h  = get_candles("BTCUSDT", "4h",  start_ms=start_ms, end_ms=end_ms)
df_1d  = get_candles("BTCUSDT", "1d",  start_ms=start_ms, end_ms=end_ms)

print(f"30m candles: {len(df_30m)}")
print(f"4h  candles: {len(df_4h)}")
print(f"1h  candles: {len(df_1h)}")
print(f"1d  candles: {len(df_1d)}")

# Count how many candles produce each signal type
hold_count = 0
buy_count  = 0
sell_count = 0
hold_reasons = {}

SAMPLE_STEP = 10  # check every 10th candle for speed

for i in range(200, len(df_30m), SAMPLE_STEP):
    hist_30m = df_30m.iloc[:i]
    candle_time = int(df_30m.index[i].timestamp() * 1000)
    hist_1h = df_1h[df_1h.index < df_30m.index[i]]
    hist_4h = df_4h[df_4h.index < df_30m.index[i]]
    hist_1d = df_1d[df_1d.index < df_30m.index[i]]

    try:
        sig = generate_signal("BTCUSDT", hist_1d, hist_4h, hist_1h, hist_30m, capital=10000.0)
        if sig.signal == "BUY":
            buy_count += 1
        elif sig.signal == "SELL":
            sell_count += 1
        else:
            hold_count += 1
            # Get the HOLD reason
            reason = sig.warnings[-1] if sig.warnings else "unknown"
            hold_reasons[reason] = hold_reasons.get(reason, 0) + 1
    except Exception as e:
        hold_count += 1

total = buy_count + sell_count + hold_count
print(f"\n--- Signal distribution (sampled every {SAMPLE_STEP} candles) ---")
print(f"BUY:  {buy_count}  ({buy_count/total*100:.1f}%)")
print(f"SELL: {sell_count}  ({sell_count/total*100:.1f}%)")
print(f"HOLD: {hold_count}  ({hold_count/total*100:.1f}%)")

print(f"\n--- Top HOLD reasons ---")
for reason, cnt in sorted(hold_reasons.items(), key=lambda x: -x[1])[:15]:
    print(f"  {cnt:4d}x  {reason}")

# Also check zone detection at one specific point
print(f"\n--- Zone analysis at mid-period ---")
mid_idx = len(df_30m) // 2
hist_30m = df_30m.iloc[:mid_idx]
df_ind = add_indicators(hist_30m)
current_price = float(df_ind["close"].iloc[-1])
print(f"Current price: {current_price:.2f}")

zones = detect_zones(hist_30m)
print(f"Zones detected: {len(zones)}")
demand = [z for z in zones if z["zone_type"] == "demand"]
supply = [z for z in zones if z["zone_type"] == "supply"]
print(f"  Demand: {len(demand)}, Supply: {len(supply)}")

in_zone = [z for z in zones if price_in_zone(current_price, z, buffer_pct=0.01)]
print(f"  Zones containing current price (1% buffer): {len(in_zone)}")

# Show regime
regime = classify_regime(hist_30m.iloc[-100:] if len(hist_30m) >= 100 else hist_30m)
print(f"\nRegime: {regime['regime']}")
print(f"Allowed: {regime['allowed_signals']}")
for d in regime['details']:
    print(f"  {d}")

# Show zone scores
h4_bias = detect_trend_bias(df_4h.iloc[:mid_idx*4//48] if len(df_4h) > 10 else df_4h)["bias"]
d1_bias = detect_trend_bias(df_1d.iloc[:mid_idx//48] if len(df_1d) > 10 else df_1d)["bias"]
print(f"\nZone scores (h4={h4_bias}, 1d={d1_bias}):")
for z in zones[:10]:
    sr = score_zone(z, hist_30m, h4_bias, d1_bias)
    in_z = price_in_zone(current_price, z, buffer_pct=0.01)
    print(f"  {z['zone_type']:8s} [{z['zone_bottom']:.0f}-{z['zone_top']:.0f}]  score={sr['score']:.0f} rating={sr['rating']}  in_zone={in_z}  rr={sr['rr_ratio']:.2f}")
