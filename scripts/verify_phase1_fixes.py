"""
Verification for the Phase 1 correctness fixes. Runs against a throwaway
SQLite DB — set DB_PATH before importing app modules:

    DB_PATH=data/db/test_fixes.db python -m scripts.verify_phase1_fixes

Checks:
  1. ATR trail distance is price-relative (works for sub-$1 coins).
  2. A close via PositionManager updates capital exactly once.
  3. Portfolio + same-direction caps trigger across DIFFERENT symbols
     (cross-process scenario).
  4. Same-candle SL+TP resolves to the stop (conservative).
"""

from __future__ import annotations

import os
import sys
import time

if os.environ.get("DB_PATH", "").endswith("lqmtf.db") or not os.environ.get("DB_PATH"):
    print("Refusing to run against the live DB. Set DB_PATH to a throwaway file, e.g.")
    print("  $env:DB_PATH='data/db/test_fixes.db'; python -m scripts.verify_phase1_fixes")
    sys.exit(1)

from app.db.connection import init_db
from app.db.migrations import run_migrations

init_db()
run_migrations()

from app.data.repository import open_paper_trade
from app.paper.account import get_paper_capital, set_paper_capital
from app.paper.position_manager import PositionManager
from app.ta.exit_engine import apply_ratchet, build_ratchet_levels

failures = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global failures
    status = "PASS" if cond else "FAIL"
    if not cond:
        failures += 1
    print(f"  [{status}] {name}{('  — ' + detail) if detail else ''}")


# ── 1. ATR trail on a sub-$1 coin ─────────────────────────────────────────────
print("\n1. ATR trail for low-priced coins")
entry, sl = 0.0820, 0.0832          # ARB-scale SELL
levels = build_ratchet_levels(entry, sl, "SELL")
# Simulate all levels consumed -> pure ATR trail; candle_low at 4R profit
new_sl, _ = apply_ratchet(
    direction="SELL", original_risk=abs(entry - sl), current_sl=sl,
    candle_high=0.0790, candle_low=0.0772, atr=0.0006,
    ratchet_level=len(levels), ratchet_levels=levels, entry=entry,
)
check("trail stop is near price, not +$1.00", 0 < new_sl < 0.10, f"new_sl={new_sl:.6f}")
check("trail tightened below entry", new_sl < entry, f"entry={entry}")

# ── 2. Capital updated exactly once on close ──────────────────────────────────
print("\n2. Single capital update on close")
SYM = "TESTAUSDT"
set_paper_capital(SYM, 10_000.0)
trade_id = open_paper_trade({
    "symbol": SYM, "signal_id": None, "direction": "BUY",
    "entry_price": 100.0, "stop_loss": 95.0, "take_profit": 110.0,
    "position_size": 10.0, "capital_at_risk": 100.0, "risk_reward": 2.0,
    "open_time": int(time.time() * 1000), "model_version": "test",
    "original_risk": 5.0, "leverage": 1,
})
pm = PositionManager([SYM])
closed = pm.check_price(SYM, 110.0)   # TP hit -> close inside _evaluate
cap = get_paper_capital(SYM)
expected_pnl = closed[0]["pnl"] if closed else 0.0
check("trade closed", bool(closed))
check(
    "capital = 10000 + pnl (exactly once)",
    abs(cap - (10_000.0 + expected_pnl)) < 0.01,
    f"capital={cap:.2f} pnl={expected_pnl:.2f}",
)

# ── 3. Cross-symbol portfolio / correlation caps ──────────────────────────────
print("\n3. Cross-symbol caps (cross-process scenario)")
for i, sym in enumerate(["AAAUSDT", "BBBUSDT", "CCCUSDT"]):
    set_paper_capital(sym, 10_000.0)
    open_paper_trade({
        "symbol": sym, "signal_id": None, "direction": "SELL",
        "entry_price": 50.0, "stop_loss": 52.0, "take_profit": 45.0,
        "position_size": 1.0, "capital_at_risk": 100.0, "risk_reward": 2.5,
        "open_time": int(time.time() * 1000) + i, "model_version": "test",
        "original_risk": 2.0, "leverage": 1,
    })
# A PM that only knows about ONE other symbol must still see all 3 open trades
pm_single = PositionManager(["DDDUSDT"])
allowed, reason = pm_single.can_open("DDDUSDT", "SELL", 1.0)
check("portfolio cap blocks 4th trade", not allowed, reason)
ok_dir, reason_dir = pm_single.can_open("DDDUSDT", "BUY", 1.0)
# still blocked by portfolio cap (3 open) — check the reason mentions portfolio
check("blocked reason is portfolio/correlation", "cap" in reason.lower(), reason)

# ── 4. Same-candle SL+TP -> stop wins ─────────────────────────────────────────
print("\n4. Conservative same-candle SL+TP")
SYM2 = "TESTBUSDT"
set_paper_capital(SYM2, 10_000.0)
# Close the 3 cap-filler trades first so this one can be evaluated cleanly
pm_caps = PositionManager(["AAAUSDT", "BBBUSDT", "CCCUSDT"])
for sym in ["AAAUSDT", "BBBUSDT", "CCCUSDT"]:
    pm_caps.check_price(sym, 45.0)    # TP for the SELLs
open_paper_trade({
    "symbol": SYM2, "signal_id": None, "direction": "BUY",
    "entry_price": 100.0, "stop_loss": 95.0, "take_profit": 110.0,
    "position_size": 10.0, "capital_at_risk": 100.0, "risk_reward": 2.0,
    "open_time": int(time.time() * 1000), "model_version": "test",
    "original_risk": 5.0, "leverage": 1,
})
pm2 = PositionManager([SYM2])
closed2 = pm2.check_candle(SYM2, high=111.0, low=94.0, close=100.0)  # spans both
check("trade closed on candle", bool(closed2))
check(
    "stop wins when both levels inside one candle",
    bool(closed2) and closed2[0]["status"] == "stopped",
    f"status={closed2[0]['status'] if closed2 else 'n/a'}",
)

print(f"\n{'ALL CHECKS PASSED' if failures == 0 else f'{failures} CHECK(S) FAILED'}")
sys.exit(1 if failures else 0)
