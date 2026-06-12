"""
Diagnose exactly why signals are blocked in a given scenario.
Usage: shows prefilter decision breakdown with per-reason counts.
"""
import warnings; warnings.filterwarnings('ignore')
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from dotenv import load_dotenv; load_dotenv()
from app.db.migrations import run_migrations; run_migrations()

import pandas as pd
from app.data.repository import get_candles
from app.ta.indicators import add_indicators
from app.ta.mtf_scorer import run_prefilter
from app.config import get_settings
from app.utils.timeframes import str_to_ms

cfg = get_settings()

SCENARIOS = [
    ("BNB BEAR Q2-2022",   "BNBUSDT", "2022-04-01", "2022-06-30"),
    ("BTC BEAR Q2-2022",   "BTCUSDT", "2022-04-01", "2022-06-30"),
    ("ETH BEAR Q2-2022",   "ETHUSDT", "2022-04-01", "2022-06-30"),
    ("SOL BEAR Q2-2022",   "SOLUSDT", "2022-04-01", "2022-06-30"),
]

def _load(sym, tf, end_ms):
    df = get_candles(sym, tf)
    if df.empty:
        return df
    return add_indicators(df[df.index < pd.Timestamp(end_ms, unit='ms', tz='UTC')])

for label, symbol, start, end in SCENARIOS:
    start_ms = str_to_ms(start)
    end_ms   = str_to_ms(end)

    df_1d  = _load(symbol, "1d",  end_ms)
    df_12h = _load(symbol, "12h", end_ms)
    df_4h  = _load(symbol, "4h",  end_ms)
    df_1h  = _load(symbol, "1h",  end_ms)
    df_30m = _load(symbol, "30m", end_ms)
    df_1w  = _load(symbol, "1w",  end_ms)

    bt_start   = pd.Timestamp(start_ms, unit='ms', tz='UTC')
    bt_candles = df_30m[df_30m.index >= bt_start]

    decision_counts = {}
    block_reasons   = {}
    SIGNAL_STEP = 2
    total = 0

    for i, (ts, _) in enumerate(bt_candles.iterrows()):
        if i % SIGNAL_STEP != 0:
            continue
        d30  = df_30m[df_30m.index < ts]
        d1h  = df_1h[df_1h.index   < ts]
        d4h  = df_4h[df_4h.index   < ts]
        d1d  = df_1d[df_1d.index   < ts]
        d12h = df_12h[df_12h.index < ts]
        d1w  = df_1w[df_1w.index   < ts]
        if len(d30) < 50 or len(d12h) < 50 or len(d1w) < 52:
            continue
        total += 1
        try:
            pf  = run_prefilter(d1w, d1d, d12h, d4h, d1h, d30, cfg)
            dec = pf.decision
            decision_counts[dec] = decision_counts.get(dec, 0) + 1
            if dec == "BLOCKED":
                r = pf.reason
                block_reasons[r] = block_reasons.get(r, 0) + 1
        except:
            pass

    print(f"\n=== {label} ({total} checks) ===")
    for k in ["LONG", "SHORT", "HOLD", "BLOCKED"]:
        v = decision_counts.get(k, 0)
        print(f"  {k:8s}: {v:4d} ({v/total*100:.1f}%)")
    print("  Block reasons:")
    for r, cnt in sorted(block_reasons.items(), key=lambda x: -x[1])[:8]:
        print(f"    {cnt:4d} ({cnt/total*100:.1f}%)  {r}")
