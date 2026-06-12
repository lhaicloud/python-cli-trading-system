"""
Verification for the Phase 5 strategy upgrades. Runs against a throwaway DB:

    $env:DB_PATH='data/db/test_phase5.db'; python -m scripts.verify_phase5

Checks:
  1. Unified portfolio equity bootstraps and absorbs PnL exactly once.
  2. Correlation-scaled risk: 2nd same-direction position risks less.
  3. Open-risk budget blocks when consumed.
  4. Daily loss circuit breaker blocks after a big realized loss today.
  5. Stagnation timeout closes an aged trade as 'timeout'.
  6. Partial TP realizes half at +1.5R; final PnL = partial + remainder;
     capital ends consistent.
  7. submit() rests a limit when price must retrace; check_pending fills it
     on touch and expires it after the deadline.
  8. Macro guard blocks signals near a scheduled FOMC event.
"""

from __future__ import annotations

import os
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

if os.environ.get("DB_PATH", "").endswith("lqmtf.db") or not os.environ.get("DB_PATH"):
    print("Refusing to run against the live DB. Set DB_PATH to a throwaway file.")
    sys.exit(1)

# Limit entries default OFF; this suite tests the machinery, so force-enable.
os.environ.setdefault("ENTRY_LIMIT_ENABLED", "1")

from app.db.connection import init_db, get_conn
from app.db.migrations import run_migrations

init_db()
run_migrations()

from app.config import get_settings
from app.data.repository import (
    get_pending_orders,
    open_paper_trade,
    update_pending_order_status,
)
from app.paper.account import (
    get_portfolio_capital,
    set_paper_capital,
    set_portfolio_capital,
)
from app.paper.position_manager import PositionManager
from app.paper.signal_filter import SignalFilter
from app.ta.signals import SignalResult

cfg = get_settings()
failures = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global failures
    status = "PASS" if cond else "FAIL"
    if not cond:
        failures += 1
    print(f"  [{status}] {name}{('  - ' + detail) if detail else ''}")


def make_sig(symbol, signal, entry, sl, tp, conf=75.0):
    return SignalResult(
        symbol=symbol, signal=signal, confidence=conf,
        market_regime="bearish_trend", daily_bias="bearish",
        h4_bias="bearish", h1_confirmation="bearish",
        zone_score=75.0, zone_rating="A", premium_discount="premium",
        entry_price=entry, stop_loss=sl, take_profit=tp,
        risk_reward=abs(tp - entry) / abs(entry - sl),
    )


def wipe_trades() -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM paper_trades")
        conn.execute("DELETE FROM pending_orders")


# ── 1. Portfolio equity single-update ─────────────────────────────────────────
print("\n1. Unified portfolio equity")
wipe_trades()
set_portfolio_capital(10_000.0)
set_paper_capital("AAAUSDT", 10_000.0)
pm = PositionManager(["AAAUSDT"])
open_paper_trade({
    "symbol": "AAAUSDT", "signal_id": None, "direction": "BUY",
    "entry_price": 100.0, "stop_loss": 95.0, "take_profit": 102.0,  # TP < 1.5R: no partial
    "position_size": 10.0, "capital_at_risk": 100.0, "risk_reward": 0.4,
    "open_time": int(time.time() * 1000), "model_version": "test",
    "original_risk": 5.0, "leverage": 1,
})
closed = pm.check_price("AAAUSDT", 102.0)
eq = get_portfolio_capital()
check("portfolio equity = 10000 + pnl", bool(closed) and abs(eq - (10_000 + closed[0]["pnl"])) < 0.01,
      f"equity={eq:.2f} pnl={closed[0]['pnl'] if closed else 'n/a'}")

# ── 2. Correlation-scaled risk ────────────────────────────────────────────────
print("\n2. Correlation-scaled risk")
wipe_trades()
set_portfolio_capital(10_000.0)
pm2 = PositionManager(["S1USDT", "S2USDT"])
sig1 = make_sig("S1USDT", "SELL", 100.0, 102.0, 95.0)
t1 = pm2.open("S1USDT", sig1, 10_000.0, None)
sig2 = make_sig("S2USDT", "SELL", 50.0, 51.0, 47.0)
t2 = pm2.open("S2USDT", sig2, 10_000.0, None)
with get_conn() as conn:
    rows = {r[0]: float(r[1]) for r in conn.execute(
        "SELECT symbol, capital_at_risk FROM paper_trades WHERE status='open'"
    ).fetchall()}
r1, r2 = rows.get("S1USDT", 0), rows.get("S2USDT", 0)
check("both trades opened", t1 is not None and t2 is not None)
check("2nd same-direction trade risks ~70%", 0 < r2 < r1 and abs(r2 / r1 - 0.7) < 0.05,
      f"risk1={r1:.2f} risk2={r2:.2f}")

# ── 3. Open-risk budget ───────────────────────────────────────────────────────
print("\n3. Open-risk budget")
# Two open trades risk 1% + 0.7% = 1.7%. Inflate their stored risk to hit 3%.
with get_conn() as conn:
    conn.execute("UPDATE paper_trades SET capital_at_risk=160 WHERE status='open'")
allowed, reason = pm2.can_open("S3USDT", "BUY", 1.0)
check("blocked when ≥3% at risk", not allowed and "risk budget" in reason.lower(), reason)

# ── 4. Daily loss circuit breaker ─────────────────────────────────────────────
print("\n4. Daily loss breaker")
wipe_trades()
set_portfolio_capital(10_000.0)
now_ms = int(time.time() * 1000)
with get_conn() as conn:
    conn.execute(
        """INSERT INTO paper_trades
           (symbol, direction, status, entry_price, stop_loss, take_profit,
            position_size, open_time, close_time, pnl)
           VALUES ('XUSDT','SELL','stopped',1,2,0.5,1,?,?,-400)""",
        (now_ms - 7_200_000, now_ms - 3_600_000),
    )
allowed, reason = pm2.can_open("S4USDT", "SELL", 1.0)
check("blocked after -400 realized today (3% of 10k = 300)",
      not allowed and "daily loss" in reason.lower(), reason)

# ── 5. Stagnation timeout ─────────────────────────────────────────────────────
print("\n5. Stagnation timeout")
wipe_trades()
set_portfolio_capital(10_000.0)
old_open = int(time.time() * 1000) - int(cfg.max_trade_age_hours * 3_600_000) - 60_000
open_paper_trade({
    "symbol": "OLDUSDT", "signal_id": None, "direction": "BUY",
    "entry_price": 100.0, "stop_loss": 90.0, "take_profit": 200.0,
    "position_size": 1.0, "capital_at_risk": 100.0, "risk_reward": 10.0,
    "open_time": old_open, "model_version": "test",
    "original_risk": 10.0, "leverage": 1,
})
pm5 = PositionManager(["OLDUSDT"])
closed = pm5.check_price("OLDUSDT", 101.0)   # neither SL nor TP
check("aged trade closed", bool(closed))
check("status is 'timeout'", bool(closed) and closed[0]["status"] == "timeout",
      closed[0]["status"] if closed else "n/a")

# ── 6. Partial TP ─────────────────────────────────────────────────────────────
print("\n6. Partial take-profit")
wipe_trades()
set_portfolio_capital(10_000.0)
open_paper_trade({
    "symbol": "PTUSDT", "signal_id": None, "direction": "BUY",
    "entry_price": 100.0, "stop_loss": 95.0, "take_profit": 115.0,
    "position_size": 10.0, "capital_at_risk": 100.0, "risk_reward": 3.0,
    "open_time": int(time.time() * 1000), "model_version": "test",
    "original_risk": 5.0, "leverage": 1,
})
pm6 = PositionManager(["PTUSDT"])
# +1.5R = 107.5 — partial only (TP 115 not reached)
res1 = pm6.check_price("PTUSDT", 108.0)
with get_conn() as conn:
    row = conn.execute(
        "SELECT position_size, partial_taken, partial_pnl FROM paper_trades "
        "WHERE symbol='PTUSDT'"
    ).fetchone()
check("trade still open after partial", not res1)
check("partial taken, size halved", row and row[1] == 1 and abs(row[0] - 5.0) < 1e-6,
      f"size={row[0] if row else '?'} partial_pnl={row[2] if row else '?'}")
eq_after_partial = get_portfolio_capital()
# Now full TP on the remainder
res2 = pm6.check_price("PTUSDT", 115.5)
eq_final = get_portfolio_capital()
total_pnl = res2[0]["pnl"] if res2 else 0
check("final pnl folds in partial", bool(res2) and abs(total_pnl - (row[2] + res2[0]["pnl_increment"])) < 0.01,
      f"pnl={total_pnl:.2f} = partial {row[2]:.2f} + rest {res2[0]['pnl_increment'] if res2 else 0:.2f}")
check("capital consistent (10000 + total pnl)", abs(eq_final - (10_000 + total_pnl)) < 0.02,
      f"equity={eq_final:.2f}")

# ── 7. Pending limit orders ───────────────────────────────────────────────────
print("\n7. Pending limit orders")
wipe_trades()
set_portfolio_capital(10_000.0)
pm7 = PositionManager(["PLUSDT"])
# SELL with entry above current price -> must rest a limit
sig = make_sig("PLUSDT", "SELL", 105.0, 107.0, 99.0)
outcome, oid = pm7.submit("PLUSDT", sig, 10_000.0, None, current_price=100.0)
check("submit -> pending", outcome == "pending" and oid is not None, f"outcome={outcome}")
check("order persisted", len(get_pending_orders("PLUSDT")) == 1)
# Price not reaching the limit: no fill
evs = pm7.check_pending("PLUSDT", high=103.0, low=100.0)
check("no fill below limit", not any(e["outcome"] == "filled" for e in evs))
# Price touches the limit: fill
evs = pm7.check_pending("PLUSDT", high=105.5, low=101.0)
filled = [e for e in evs if e["outcome"] == "filled"]
check("fills on touch", len(filled) == 1 and filled[0]["trade_id"] is not None)
with get_conn() as conn:
    row = conn.execute(
        "SELECT entry_price FROM paper_trades WHERE symbol='PLUSDT' AND status='open'"
    ).fetchone()
check("fill at the limit price (no slippage)", row and abs(row[0] - 105.0) < 1e-6,
      f"entry={row[0] if row else '?'}")
# Expiry path
wipe_trades()
outcome, oid = pm7.submit("PLUSDT", sig, 10_000.0, None, current_price=100.0)
with get_conn() as conn:
    conn.execute("UPDATE pending_orders SET expiry_ms=? WHERE id=?",
                 (int(time.time() * 1000) - 1000, oid))
evs = pm7.check_pending("PLUSDT", high=200.0, low=1.0)
check("expired order does not fill",
      len(evs) == 1 and evs[0]["outcome"] == "expired", str([e["outcome"] for e in evs]))

# ── 8. Macro event guard ──────────────────────────────────────────────────────
print("\n8. Macro event guard")
filt = SignalFilter()
sig8 = make_sig("BTCUSDT", "SELL", 100.0, 102.0, 95.0)
# 2026-06-17 18:00 UTC FOMC decision — 30 min before
from datetime import datetime, timezone
fomc_ms = int(datetime(2026, 6, 17, 17, 30, tzinfo=timezone.utc).timestamp() * 1000)
ok, reason = filt._macro_events(make_sig("BTCUSDT", "SELL", 100, 102, 95))
filt._now_ms = fomc_ms
blocked_ok, blocked_reason = filt._macro_events(sig8)
check("blocked near FOMC", not blocked_ok and "Macro guard" in blocked_reason, blocked_reason)
filt._now_ms = int(datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)
ok2, _ = filt._macro_events(sig8)
check("allowed on a quiet day", ok2)

print(f"\n{'ALL CHECKS PASSED' if failures == 0 else f'{failures} CHECK(S) FAILED'}")
sys.exit(1 if failures else 0)
