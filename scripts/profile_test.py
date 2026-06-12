"""Profile one signal evaluation to find the bottleneck."""
import sys, io, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from dotenv import load_dotenv; load_dotenv()
from app.db.migrations import run_migrations; run_migrations()

from app.data.repository import get_candles
from app.ta.indicators import add_indicators
from app.ta.signals import generate_signal
from app.ta.zones import detect_zones
from app.ta.liquidity import detect_liquidity_levels
from app.ta.regime import classify_regime
from app.ta.market_structure import detect_structure
from app.utils.timeframes import str_to_ms

# Load data
start_ms = str_to_ms("2023-01-01")
end_ms   = str_to_ms("2023-03-31")
df_30m = get_candles("BTCUSDT", "30m", start_ms=start_ms-500*30*60*1000, end_ms=end_ms)
df_1h  = get_candles("BTCUSDT", "1h",  start_ms=start_ms-500*60*60*1000, end_ms=end_ms)
df_4h  = get_candles("BTCUSDT", "4h",  start_ms=start_ms-500*4*60*60*1000, end_ms=end_ms)
df_1d  = get_candles("BTCUSDT", "1d",  start_ms=start_ms-365*24*60*60*1000, end_ms=end_ms)

# Pre-compute indicators
t0 = time.time()
df_30m = add_indicators(df_30m)
df_1h  = add_indicators(df_1h)
df_4h  = add_indicators(df_4h)
df_1d  = add_indicators(df_1d)
print(f"Indicator precompute: {time.time()-t0:.2f}s")
print(f"30m rows: {len(df_30m)}")

# Profile one signal call at a mid-period index
i = len(df_30m) // 2
ts = df_30m.index[i]
hist_30m = df_30m.iloc[:i]
hist_1h  = df_1h[df_1h.index < ts]
hist_4h  = df_4h[df_4h.index < ts]
hist_1d  = df_1d[df_1d.index < ts]

print(f"\nSlice sizes: 30m={len(hist_30m)}, 1h={len(hist_1h)}, 4h={len(hist_4h)}, 1d={len(hist_1d)}")

# Profile each component
t0 = time.time(); zones = detect_zones(hist_30m); print(f"detect_zones:    {time.time()-t0:.3f}s  ({len(zones)} zones)")
t0 = time.time(); liq = detect_liquidity_levels(hist_30m); print(f"detect_liq:      {time.time()-t0:.3f}s  ({len(liq)} levels)")
t0 = time.time(); regime = classify_regime(hist_4h); print(f"classify_regime: {time.time()-t0:.3f}s")
t0 = time.time(); struct = detect_structure(hist_4h); print(f"detect_struct:   {time.time()-t0:.3f}s")

# Full signal
t0 = time.time()
sig = generate_signal("BTCUSDT", hist_1d, hist_4h, hist_1h, hist_30m)
print(f"generate_signal: {time.time()-t0:.3f}s => {sig.signal} (conf={sig.confidence:.0f})")

# Time 10 consecutive signal evaluations
print("\nTiming 10 signal evaluations...")
times = []
for j in range(10):
    idx = len(df_30m) // 2 + j * 5
    ts2 = df_30m.index[idx]
    h30 = df_30m.iloc[:idx]
    h1  = df_1h[df_1h.index < ts2]
    h4  = df_4h[df_4h.index < ts2]
    h1d = df_1d[df_1d.index < ts2]
    t0 = time.time()
    _ = generate_signal("BTCUSDT", h1d, h4, h1, h30)
    times.append(time.time() - t0)

print(f"Avg: {sum(times)/len(times):.3f}s  Min: {min(times):.3f}s  Max: {max(times):.3f}s")
print(f"Estimated for 644 evals: {sum(times)/len(times)*644:.0f}s = {sum(times)/len(times)*644/60:.1f}min")
