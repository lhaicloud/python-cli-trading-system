"""Print final confirmed config."""
import warnings; warnings.filterwarnings('ignore')
from app.config import get_settings
cfg = get_settings()
print("=== Final Config State ===")
print(f"  min_zone_score:          {cfg.min_zone_score}")
print(f"  min_zone_score_no_model: {cfg.min_zone_score_no_model}")
print(f"  min_signal_confidence:   {cfg.min_signal_confidence}")
print(f"  min_rr_ratio:            {cfg.min_rr_ratio}")
print(f"  mtf_min_score:           {cfg.mtf_min_score}")
print(f"  mtf_min_score_gap:       {cfg.mtf_min_score_gap}")
print(f"  mtf_ema200_strict:       {cfg.mtf_ema200_strict}")
w = cfg.mtf_tf_weight_1w + cfg.mtf_tf_weight_1d + cfg.mtf_tf_weight_12h + cfg.mtf_tf_weight_4h + cfg.mtf_tf_weight_1h + cfg.mtf_tf_weight_30m
print(f"  MTF weights:  1W={cfg.mtf_tf_weight_1w} 1D={cfg.mtf_tf_weight_1d} 12H={cfg.mtf_tf_weight_12h} 4H={cfg.mtf_tf_weight_4h} 1H={cfg.mtf_tf_weight_1h} 30M={cfg.mtf_tf_weight_30m} (sum={w:.2f})")

from app.ta.exit_engine import build_ratchet_levels
levels = build_ratchet_levels(100, 90, "BUY")
print(f"\n=== Ratchet Levels ===")
for l in levels:
    print(f"  {l}")
