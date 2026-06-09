from dotenv import load_dotenv; load_dotenv()
from app.ta.leverage import dynamic_leverage
from app.utils.math_utils import position_size
from app.config import get_settings

cfg = get_settings()
print(f"max_leverage setting: {cfg.max_leverage}x")

class HighConvSig:
    confidence=82; zone_score=78; risk_reward=2.8
    long_score=5; short_score=60; market_regime='bearish_trend'
    model_version='sell_v_123'

class MedConvSig:
    confidence=73; zone_score=68; risk_reward=2.1
    long_score=8; short_score=48; market_regime='distribution'
    model_version='sell_v_123'

class WeakSig:
    confidence=66; zone_score=62; risk_reward=1.9
    long_score=10; short_score=55; market_regime='choppy'
    model_version='rule_based_v1'

class LosingStreakSig:
    confidence=79; zone_score=75; risk_reward=2.5
    long_score=5; short_score=58; market_regime='bearish_trend'
    model_version='sell_v_123'

losing_trades = [{"pnl": -50}, {"pnl": -60}, {"pnl": -45}]

print("\n--- Signal -> Leverage ---")
for label, sig, trades in [
    ("High conviction (no losing streak)", HighConvSig(), []),
    ("Medium conviction               ", MedConvSig(),   []),
    ("Weak / choppy / rule-based      ", WeakSig(),      []),
    ("High conv + 3 consecutive losses", LosingStreakSig(), losing_trades),
]:
    lev  = dynamic_leverage(sig, recent_trades=trades, max_leverage=5)
    base = position_size(10000, 1.0, 74000, 73000)
    pos  = position_size(10000, 1.0, 74000, 73000, leverage=lev)
    risk = pos * abs(74000 - 73000)
    print(f"  {label}: {lev}x   pos={pos:.3f} BTC  risk=${risk:,.0f}")

print("\nImports OK, leverage logic working.")
