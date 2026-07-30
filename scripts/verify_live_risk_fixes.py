"""
Verify the 2026-07-30 live risk-accounting fixes against a throwaway DB.

Covers:
  1. _finalize_entry writes capital_at_risk in USD and original_risk as the
     entry->stop price distance (previously both held the same USD figure).
  2. capital_at_risk tracks the size that actually went on, not the sizing
     intent, after the margin clamp.
  3. The live_min_fill_fraction floor skips noise-sized fills.
  4. The open-risk budget guard blocks once open risk reaches the cap.

Run against a scratch DB:
    DB_PATH=data/db/verify_live_risk.db python -m scripts.verify_live_risk_fixes
"""

from __future__ import annotations

import os
import sys
import threading

if "DB_PATH" not in os.environ:
    sys.exit("Refusing to run: set DB_PATH to a throwaway DB first.")

from app.config import get_settings                       # noqa: E402
from app.db.connection import get_conn, init_db           # noqa: E402
from app.db.migrations import run_migrations              # noqa: E402
from app.live.executor import LiveExecutor                # noqa: E402

FAILURES: list[str] = []


def check(label: str, got, want, tol: float = 1e-6) -> None:
    ok = abs(got - want) <= tol if isinstance(want, (int, float)) else got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: got {got!r}, want {want!r}")
    if not ok:
        FAILURES.append(label)


class FakeClient:
    """Minimal stand-in for BinanceExchangeClient."""

    def __init__(self, equity=5000.0, balance=5000.0):
        self._equity, self._balance = equity, balance

    def get_equity(self):            return self._equity
    def get_balance(self):           return self._balance
    def get_mark_price(self, s):     return 100.0
    def round_price(self, s, p):     return round(p, 4)
    def round_qty(self, s, q):       return round(q, 3)
    def set_leverage(self, s, l):    return None

    def place_stop_market(self, *a, **k):        return {"algoId": "sl-1"}
    def place_take_profit_market(self, *a, **k): return {"algoId": "tp-1"}
    def place_market_order(self, *a, **k):       return {"orderId": 1, "avgPrice": "100"}


def reset_db() -> None:
    init_db()
    run_migrations()          # live_trades is created here, not in schema.sql
    with get_conn() as conn:
        conn.execute("DELETE FROM live_trades")
        conn.commit()


def main() -> int:
    cfg = get_settings()
    reset_db()
    ex = LiveExecutor(FakeClient(), threading.Lock())

    # ── 1 + 2. Risk columns reflect the filled size, in the right units ──────
    print("\n1/2. _finalize_entry risk columns")
    fill, stop, size = 100.0, 98.0, 12.5          # 2.00 price risk, 12.5 units
    tid = ex._finalize_entry(
        symbol="TESTUSDT", direction="BUY", actual_fill=fill, stop_loss=stop,
        take_profit=106.0, position_size=size, leverage=1, rr=3.0,
        signal_id=None, model_version=None, exchange_entry_id="e-1",
    )
    with get_conn() as conn:
        row = conn.execute(
            "SELECT capital_at_risk, original_risk FROM live_trades WHERE id=?", (tid,)
        ).fetchone()
    check("capital_at_risk is USD (size x price-risk)", row[0], size * abs(fill - stop))
    check("original_risk is the price distance",        row[1], abs(fill - stop))
    check("the two columns are no longer identical",    row[0] != row[1], True)

    # The historical XRPUSDT id 33 shape: $46 intent, $80 notional actually on.
    print("\n   regression: XRPUSDT 2026-07-27 shape")
    tid2 = ex._finalize_entry(
        symbol="XRPUSDT", direction="SELL", actual_fill=1.1046, stop_loss=1.1115,
        take_profit=1.09, position_size=72.8, leverage=1, rr=2.52,
        signal_id=None, model_version=None, exchange_entry_id="e-2",
    )
    with get_conn() as conn:
        car = conn.execute(
            "SELECT capital_at_risk FROM live_trades WHERE id=?", (tid2,)
        ).fetchone()[0]
    check("risk is ~$0.50, not the $46.19 intent", round(car, 2), 0.50, tol=0.01)

    # ── 3. Fill-fraction floor ───────────────────────────────────────────────
    print(f"\n3. live_min_fill_fraction floor = {cfg.live_min_fill_fraction} "
          f"(enforce={cfg.live_min_fill_enforce})")
    cases = [   # (label, intended, filled, below_floor)
        ("XRPUSDT Jul27 noise fill", 4178.532442,   72.800041, True),
        ("SOLUSDT Jul26 noise fill",   61.878422,    3.181052, True),
        ("SUIUSDT Jul27 half size",  5727.079371, 2370.832449, False),
        ("XRPUSDT Jul25 near-full",  4206.428611, 3996.107181, False),
    ]
    for label, intended, filled, below in cases:
        check(label, (filled / intended) < cfg.live_min_fill_fraction, below)

    # Shadow mode must classify identically but never block. Enforcement is the
    # only thing the flag changes — a flag that also moved the threshold would
    # make the shadow log a poor predictor of what enforcing would do.
    print("\n   shadow vs enforce")
    below_floor = [c for c in cases if c[3]]
    for enforce in (False, True):
        blocked = [c for c in below_floor if enforce]
        check(f"enforce={enforce}: blocks {len(blocked)} of {len(below_floor)} "
              f"below-floor fills", len(blocked), len(below_floor) if enforce else 0)
    check("shipping default is shadow (no live behaviour change)",
          cfg.live_min_fill_enforce, False)

    # ── 4. Open-risk budget guard ────────────────────────────────────────────
    print(f"\n4. open-risk budget = {cfg.max_open_risk_pct}% of equity")
    reset_db()
    equity = 5000.0
    ex2 = LiveExecutor(FakeClient(equity=equity), threading.Lock())
    ok, reason = ex2._check_guards("TESTUSDT", "BUY")
    check("empty book passes", ok, True)

    budget = equity * cfg.max_open_risk_pct / 100
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO live_trades (symbol, direction, status, entry_price, "
            "stop_loss, position_size, capital_at_risk, open_time) "
            "VALUES ('OTHERUSDT','BUY','open',100,98,1,?,0)", (budget + 1,),
        )
        conn.commit()
    ok, reason = ex2._check_guards("TESTUSDT", "BUY")
    check("book at the cap blocks", ok, False)
    # cp1252 consoles can't render the guard string's ">=" glyph
    print("       reason:", reason.encode("ascii", "replace").decode())

    print("\n" + ("ALL CHECKS PASSED" if not FAILURES
                  else f"{len(FAILURES)} FAILURE(S): {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
