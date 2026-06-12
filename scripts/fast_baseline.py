"""
Fast baseline diagnostic — skips every 5 candles to estimate signal rates,
then runs a small 1-month full backtest to get actual trade metrics.
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from dotenv import load_dotenv; load_dotenv()
from app.db.migrations import run_migrations; run_migrations()

from app.data.repository import get_candles
from app.ta.signals import generate_signal
from app.ta.zones import detect_zones, price_in_zone
from app.ta.regime import classify_regime
from app.ta.trend import detect_trend_bias
from app.ta.zone_scoring import score_zone
from app.ta.indicators import add_indicators
from app.config import get_settings
from app.utils.timeframes import str_to_ms

cfg = get_settings()
print(f"Config: min_signal_confidence={cfg.min_signal_confidence}, min_zone_score={cfg.min_zone_score}, min_rr={cfg.min_rr_ratio}")

# ─── Diagnostic over 3 months of bull market ─────────────────────────────────
start_ms = str_to_ms("2023-01-01")
end_ms   = str_to_ms("2023-03-31")

df_30m = get_candles("BTCUSDT", "30m", start_ms=start_ms, end_ms=end_ms)
df_1h  = get_candles("BTCUSDT", "1h",  start_ms=start_ms, end_ms=end_ms)
df_4h  = get_candles("BTCUSDT", "4h",  start_ms=start_ms, end_ms=end_ms)
df_1d  = get_candles("BTCUSDT", "1d",  start_ms=start_ms, end_ms=end_ms)

print(f"\nData loaded: 30m={len(df_30m)}, 1h={len(df_1h)}, 4h={len(df_4h)}, 1d={len(df_1d)}")

hold_reasons = {}
buy_count = sell_count = hold_count = 0

# Sample every 20th candle for speed
STEP = 20
for i in range(200, len(df_30m), STEP):
    ts = df_30m.index[i]
    hist_30m = df_30m.iloc[:i]
    hist_1h  = df_1h[df_1h.index < ts]
    hist_4h  = df_4h[df_4h.index < ts]
    hist_1d  = df_1d[df_1d.index < ts]
    try:
        sig = generate_signal("BTCUSDT", hist_1d, hist_4h, hist_1h, hist_30m)
        if sig.signal == "BUY":
            buy_count += 1
        elif sig.signal == "SELL":
            sell_count += 1
        else:
            hold_count += 1
            # Extract reason from last warning
            for w in reversed(sig.warnings):
                if "HOLD reason:" in w:
                    reason = w.replace("HOLD reason: ", "").split(" (")[0][:60]
                    hold_reasons[reason] = hold_reasons.get(reason, 0) + 1
                    break
    except Exception as e:
        hold_count += 1
        hold_reasons[f"ERROR: {str(e)[:40]}"] = hold_reasons.get(f"ERROR: {str(e)[:40]}", 0) + 1

total = buy_count + sell_count + hold_count
print(f"\n=== Signal Distribution (every {STEP}th 30m candle, Q1-2023) ===")
print(f"BUY:  {buy_count}  ({buy_count/total*100:.1f}%)")
print(f"SELL: {sell_count}  ({sell_count/total*100:.1f}%)")
print(f"HOLD: {hold_count}  ({hold_count/total*100:.1f}%)")
print(f"Total sampled: {total}")

print(f"\n=== Top HOLD Reasons ===")
for reason, cnt in sorted(hold_reasons.items(), key=lambda x: -x[1])[:12]:
    print(f"  {cnt:4d}x  {reason}")

# ─── Zone availability analysis ───────────────────────────────────────────────
print(f"\n=== Zone Availability Analysis ===")
period_results = {"in_zone": 0, "zone_score_low": 0, "no_zone_near": 0, "total_checked": 0}
for i in range(200, len(df_30m), STEP):
    ts = df_30m.index[i]
    hist_30m = df_30m.iloc[:i]
    hist_1h  = df_1h[df_1h.index < ts]
    hist_4h  = df_4h[df_4h.index < ts]
    hist_1d  = df_1d[df_1d.index < ts]

    if len(hist_30m) < 50:
        continue

    price = float(add_indicators(hist_30m)["close"].iloc[-1])
    zones = detect_zones(hist_30m)
    h4b = detect_trend_bias(hist_4h)["bias"] if len(hist_4h) >= 50 else "neutral"
    d1b = detect_trend_bias(hist_1d)["bias"] if len(hist_1d) >= 50 else "neutral"

    period_results["total_checked"] += 1
    in_zone_found = False
    score_too_low = False

    for z in zones:
        if price_in_zone(price, z, buffer_pct=0.01):
            in_zone_found = True
            sr = score_zone(z, hist_30m, h4b, d1b)
            if sr["score"] < cfg.min_zone_score:
                score_too_low = True

    if in_zone_found and not score_too_low:
        period_results["in_zone"] += 1
    elif in_zone_found and score_too_low:
        period_results["zone_score_low"] += 1
    else:
        period_results["no_zone_near"] += 1

t = period_results["total_checked"]
print(f"  Price in qualifying zone: {period_results['in_zone']} ({period_results['in_zone']/t*100:.1f}%)")
print(f"  Price in zone but score<{cfg.min_zone_score}: {period_results['zone_score_low']} ({period_results['zone_score_low']/t*100:.1f}%)")
print(f"  No zone near price: {period_results['no_zone_near']} ({period_results['no_zone_near']/t*100:.1f}%)")

# ─── Regime distribution ──────────────────────────────────────────────────────
print(f"\n=== Regime Distribution (4H) ===")
regimes = {}
for i in range(200, len(df_4h), 5):
    ts = df_4h.index[i]
    hist_4h_seg = df_4h.iloc[:i]
    if len(hist_4h_seg) < 50:
        continue
    r = classify_regime(hist_4h_seg)
    regimes[r["regime"]] = regimes.get(r["regime"], 0) + 1

for regime, cnt in sorted(regimes.items(), key=lambda x: -x[1]):
    print(f"  {cnt:4d}x  {regime}")

print("\nDone.")
