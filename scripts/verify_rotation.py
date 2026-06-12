"""
Verification for the rotation overhaul + Phase B improvements.

Run against the DEV DB copy (read-mostly; the few rows it inserts are
removed at the end):

    $env:DB_PATH='data/db/lqmtf_dev.db'; python -m scripts.verify_rotation

Checks:
  1. REGIME_RANK aligns with the live filter (distribution unattractive).
  2. position_size caps notional at capital × leverage.
  3. Funding-rate storage round-trip + staleness window.
  4. Validation SQL counts only current-engine runs.
  5. Funding filter blocks crowded shorts in backtest context.
  6. Pool-restricted scan returns only pool symbols, conviction-sorted.
"""

from __future__ import annotations

import os
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

if os.environ.get("DB_PATH", "").endswith("lqmtf.db") or not os.environ.get("DB_PATH"):
    print("Refusing to run against the live DB. Set DB_PATH to the dev copy.")
    sys.exit(1)

from app.db.migrations import run_migrations
run_migrations()

from app.backtesting.engine import ENGINE_VERSION
from app.db.connection import get_conn
from app.data.repository import get_funding_rate_at, save_funding_rates
from app.paper.signal_filter import SignalFilter
from app.ta.signals import SignalResult
from app.utils.math_utils import position_size
import scan_universe

failures = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global failures
    status = "PASS" if cond else "FAIL"
    if not cond:
        failures += 1
    print(f"  [{status}] {name}{('  - ' + detail) if detail else ''}")


# ── 1. REGIME_RANK alignment ──────────────────────────────────────────────────
print("\n1. REGIME_RANK alignment with SignalFilter")
check("distribution ranks 0 (filter blocks it)",
      scan_universe.REGIME_RANK["distribution"] == 0)
check("trends still rank highest",
      scan_universe.REGIME_RANK["bullish_trend"] == 5
      and scan_universe.REGIME_RANK["bearish_trend"] == 5)

# ── 2. Notional cap ───────────────────────────────────────────────────────────
print("\n2. Notional cap in position_size")
# Live trade #23 parameters: $10k capital, 1% risk, tight 0.47% stop
units = position_size(10_000, 1.0, entry=73_847.0, sl=74_196.15, leverage=1)
notional = units * 73_847.0
check("notional capped at capital (1x)", notional <= 10_000 + 1, f"notional=${notional:,.0f}")
units5 = position_size(10_000, 1.0, entry=73_847.0, sl=74_196.15, leverage=5)
check("5x leverage allows 5x notional",
      units5 * 73_847.0 <= 50_000 + 1 and units5 > units,
      f"notional=${units5 * 73_847.0:,.0f}")
# Wide stop unaffected by the cap
units_wide = position_size(10_000, 1.0, entry=100.0, sl=95.0, leverage=1)
check("wide-stop sizing unchanged", abs(units_wide - 20.0) < 1e-9, f"units={units_wide}")

# ── 3. Funding storage round-trip ─────────────────────────────────────────────
print("\n3. Funding-rate storage")
now_ms = int(time.time() * 1000)
save_funding_rates("TESTFUSDT", [
    {"funding_time": now_ms - 8 * 3_600_000, "rate": -0.0012},
    {"funding_time": now_ms - 16 * 3_600_000, "rate": 0.0003},
])
rate = get_funding_rate_at("TESTFUSDT", now_ms)
check("returns latest rate <= ts", rate is not None and abs(rate - (-0.0012)) < 1e-9,
      f"rate={rate}")
stale = get_funding_rate_at("TESTFUSDT", now_ms + 30 * 3_600_000)
check("stale data (>24h) returns None", stale is None)

# ── 4. Validation counts only current-engine runs ─────────────────────────────
print("\n4. v2-only validation")
with get_conn() as conn:
    conn.execute(
        """INSERT INTO backtest_runs (symbol, start_time, end_time, initial_capital,
           total_trades, win_rate, net_profit, model_version)
           VALUES ('FOOTESTUSDT', 0, 1, 10000, 50, 0.9, 99999, 'rule_based_v1')"""
    )
    conn.execute(
        """INSERT INTO backtest_runs (symbol, start_time, end_time, initial_capital,
           total_trades, win_rate, net_profit, model_version)
           VALUES ('FOOTESTUSDT', 0, 1, 10000, 10, 0.6, 500, ?)""",
        (ENGINE_VERSION,),
    )
    row = conn.execute(
        """SELECT COUNT(*) FROM backtest_runs
           WHERE symbol='FOOTESTUSDT' AND model_version = ?
             AND net_profit > 0 AND win_rate >= 0.45 AND total_trades >= 5""",
        (ENGINE_VERSION,),
    ).fetchone()
check("old v1 run ignored, v2 run counts", row[0] == 1, f"v2 profitable runs={row[0]}")

# ── 5. Funding filter in backtest context ─────────────────────────────────────
print("\n5. Funding filter (backtest path)")
import pandas as pd
filt = SignalFilter()
filt._df_30m = pd.DataFrame({"close": [1.0]})   # marks backtest context
filt._now_ms = now_ms
sig = SignalResult(symbol="TESTFUSDT", signal="SELL", confidence=75.0,
                   entry_price=100.0, stop_loss=102.0, take_profit=95.0,
                   risk_reward=2.5)
ok, reason = filt._funding_rate(sig)
check("crowded short blocked from stored rate", not ok and "Funding" in reason, reason)
sig_buy = SignalResult(symbol="TESTFUSDT", signal="BUY", confidence=75.0,
                       entry_price=100.0, stop_loss=98.0, take_profit=105.0,
                       risk_reward=2.5)
ok_buy, _ = filt._funding_rate(sig_buy)
check("BUY allowed when funding negative", ok_buy)

# ── 6. Pool-restricted scan ───────────────────────────────────────────────────
print("\n6. Pool-restricted scan (live data, may take ~30s)")
pool = ["BTCUSDT", "ETHUSDT"]
ranked = scan_universe.scan_and_rank(validated_only=False, pool=pool)
syms = [r["symbol"] for r in ranked]
check("only pool symbols returned", set(syms) <= set(pool) and len(syms) > 0, str(syms))
check("sorted by conviction desc",
      all(ranked[i]["conv_score"] >= ranked[i + 1]["conv_score"]
          for i in range(len(ranked) - 1)))

# ── Cleanup test rows ─────────────────────────────────────────────────────────
with get_conn() as conn:
    conn.execute("DELETE FROM backtest_runs WHERE symbol='FOOTESTUSDT'")
    conn.execute("DELETE FROM funding_rates WHERE symbol='TESTFUSDT'")

print(f"\n{'ALL CHECKS PASSED' if failures == 0 else f'{failures} CHECK(S) FAILED'}")
sys.exit(1 if failures else 0)
