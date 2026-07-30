"""
Head-to-head: resting limit at the zone vs market entry at the drifted mark,
for every entry the live drift gate refused.

Context. Measured inside paper (identical fill/exit model, so execution is held
constant), the trades live took won 18.2% and the trades it refused won 80.0%.
Live's filter is inverted. But paper earns that 80% at the *idealized zone
price*, which live cannot reach — and the 2026-07-16 counterfactual found that
chasing those same setups at the drifted price destroys most of the edge. Both
can be true, which means the real question was never asked: is a resting limit
that fills only ~29% of the time better or worse than market-entering into the
cohort the gate leaves behind?

Method. Take every "SKIPPED — R:R at mark price" the executor logged, recover
the signal's stop/target, and replay BOTH arms against the same mainnet 30m
candles:

  Arm A (limit) : rest at the signal's zone entry, expire after
                  entry_limit_expiry_candles. No entry slippage — a limit fills
                  at its own price or not at all. Unfilled = flat, 0R.
  Arm B (market): fill immediately at the mark price the gate saw, with entry
                  slippage. This is the arm the gate is currently refusing.

Both then run the same exit engine: partial TP at partial_tp_r (closing
partial_tp_fraction) then a breakeven stop on the remainder, max_trade_age_hours
timeout, fees on both sides and slippage on stop-type exits. Results are in R
so unequal position sizes can't distort the comparison.

Same-candle SL+TP resolves by proximity to the candle open, ties to SL —
the backtest engine's convention.

Usage (on the live host, read-only):
    journalctl -u lqmtf-live.service --since '30 days ago' --no-pager -o short-iso \
      | grep -E 'SKIPPED — R:R at mark price' | sed -E '...' > /tmp/skips.csv
    python -m scripts.limit_vs_market_headtohead /tmp/skips.csv
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone

from app.config import get_settings
from app.db.connection import get_conn

cfg = get_settings()

FEE      = cfg.backtest_fee_pct / 100
SLIP     = cfg.backtest_slippage_pct / 100
PARTIAL_R = cfg.partial_tp_r
PARTIAL_F = cfg.partial_tp_fraction
EXPIRY_C  = cfg.entry_limit_expiry_candles
MAX_HOLD_MS = int(cfg.max_trade_age_hours * 3_600_000)
CANDLE_MS   = 1_800_000


# ── data ──────────────────────────────────────────────────────────────────────

def load_skips(path: str) -> list[dict]:
    out = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or "|" not in line:
            continue
        iso, sym, mark, rr_mark, sig_ent, promised = line.split("|")
        ts = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%S%z").astimezone(timezone.utc)
        out.append({
            "ts_ms":    int(ts.timestamp() * 1000),
            "symbol":   sym,
            "mark":     float(mark),
            "rr_mark":  float(rr_mark),
            "sig_entry": float(sig_ent),
            "promised": float(promised),
        })
    return out


def find_signal(conn, skip: dict) -> dict | None:
    """Recover stop/target for the signal the executor was acting on."""
    row = conn.execute(
        """
        SELECT signal, entry_price, stop_loss, take_profit
        FROM signals
        WHERE symbol = ? AND signal IN ('BUY','SELL')
          AND abs(entry_price - ?) < ?
          AND timestamp BETWEEN ? AND ?
        ORDER BY abs(timestamp - ?) LIMIT 1
        """,
        (skip["symbol"], skip["sig_entry"], max(skip["sig_entry"] * 1e-4, 1e-9),
         skip["ts_ms"] - 600_000, skip["ts_ms"] + 600_000, skip["ts_ms"]),
    ).fetchone()
    if not row:
        return None
    return {"dir": row[0], "entry": float(row[1]),
            "sl": float(row[2]), "tp": float(row[3])}


def load_candles(conn, symbol: str, start_ms: int, end_ms: int) -> list[tuple]:
    return conn.execute(
        """
        SELECT open_time, open, high, low, close FROM candles
        WHERE symbol=? AND timeframe='30m' AND open_time BETWEEN ? AND ?
        ORDER BY open_time
        """,
        (symbol, start_ms, end_ms),
    ).fetchall()


# ── exit engine (shared by both arms) ─────────────────────────────────────────

def simulate_exit(entry: float, sl: float, tp: float, direction: str,
                  candles: list[tuple]) -> tuple[float, str]:
    """Return (R multiple net of costs, outcome label)."""
    risk = abs(entry - sl)
    if risk <= 0 or not candles:
        return 0.0, "no-data"

    long_ = direction == "BUY"
    rr    = abs(tp - entry) / risk

    partial_px = entry + PARTIAL_R * risk if long_ else entry - PARTIAL_R * risk
    # +1.5R past the target leaves no room for a two-stage exit
    single = (partial_px >= tp) if long_ else (partial_px <= tp)

    # Round-trip fee, expressed in R
    fee_R  = (2 * FEE * entry) / risk
    slip_R = (SLIP * entry) / risk        # stop-type exits are market orders

    banked = 0.0
    took_partial = False
    stop = sl
    deadline = candles[0][0] + MAX_HOLD_MS

    for open_t, o, h, l, c in candles:
        if open_t > deadline:
            break
        hit_stop = (l <= stop) if long_ else (h >= stop)
        hit_tp   = (h >= tp)   if long_ else (l <= tp)
        hit_part = (not single and not took_partial
                    and ((h >= partial_px) if long_ else (l <= partial_px)))

        if hit_stop and hit_tp:
            # Both touched in one candle — assume whichever sits closer to the
            # open resolved first; a tie goes to the stop (conservative).
            hit_tp = abs(tp - o) < abs(stop - o)
            hit_stop = not hit_tp

        if hit_stop:
            if took_partial:                      # stop was moved to breakeven
                return banked - slip_R - fee_R, "partial+BE"
            return -1.0 - slip_R - fee_R, "stopped"

        if hit_part and not hit_tp:
            banked += PARTIAL_F * PARTIAL_R
            took_partial = True
            stop = entry                          # breakeven ratchet
            continue

        if hit_tp:
            frac = (1 - PARTIAL_F) if took_partial else 1.0
            return banked + frac * rr - fee_R, "target"

    # Ran to the age limit (or out of data) — mark to the last close in window
    last = entry
    for open_t, o, h, l, c in candles:
        if open_t <= deadline:
            last = c
    move = (last - entry) if long_ else (entry - last)
    frac = (1 - PARTIAL_F) if took_partial else 1.0
    return banked + frac * (move / risk) - fee_R, "timeout"


# ── the two arms ──────────────────────────────────────────────────────────────

def arm_limit(sig: dict, candles: list[tuple]) -> tuple[float, str]:
    """Rest at the zone price; fill only if price comes back to it."""
    if not candles:
        return 0.0, "no-data"
    z     = sig["entry"]
    long_ = sig["dir"] == "BUY"
    for i, (open_t, o, h, l, c) in enumerate(candles[:EXPIRY_C]):
        touched = (l <= z) if long_ else (h >= z)
        if touched:
            # Fills at its own price — no entry slippage.
            return simulate_exit(z, sig["sl"], sig["tp"], sig["dir"], candles[i:])
    return 0.0, "unfilled"


def arm_market(sig: dict, mark: float, candles: list[tuple]) -> tuple[float, str]:
    """Take the drifted mark immediately, paying entry slippage."""
    long_ = sig["dir"] == "BUY"
    fill  = mark * (1 + SLIP) if long_ else mark * (1 - SLIP)
    # Drift can push the mark past the stop entirely — unplayable either way.
    if (long_ and fill <= sig["sl"]) or (not long_ and fill >= sig["sl"]):
        return 0.0, "beyond-stop"
    # Or past the target, leaving no reward to collect. Entering a short below
    # its own take-profit is not a trade; without this the exit engine sees the
    # target already touched and books an instant fictitious win. 36% of the
    # logged skips are this case (the gate reports them as negative R:R).
    reward = (sig["tp"] - fill) if long_ else (fill - sig["tp"])
    if reward <= 0:
        return 0.0, "no-reward"
    return simulate_exit(fill, sig["sl"], sig["tp"], sig["dir"], candles)


# ── report ────────────────────────────────────────────────────────────────────

def summarise(name: str, rows: list[tuple[float, str]]) -> None:
    n     = len(rows)
    skipped_labels = ("unfilled", "beyond-stop", "no-reward", "no-data")
    taken = [r for r, o in rows if o not in skipped_labels]
    wins  = [r for r in taken if r > 0]
    total = sum(r for r, _ in rows)
    print(f"\n  {name}")
    print(f"    opportunities   : {n}")
    print(f"    trades taken    : {len(taken)}  ({100*len(taken)/n:.0f}% participation)")
    if taken:
        print(f"    win rate        : {100*len(wins)/len(taken):.1f}%  "
              f"({len(wins)}/{len(taken)})")
        print(f"    avg R per trade : {sum(taken)/len(taken):+.3f}")
    print(f"    TOTAL R         : {total:+.2f}")
    outcomes: dict[str, int] = {}
    for _, o in rows:
        outcomes[o] = outcomes.get(o, 0) + 1
    print(f"    outcomes        : "
          + ", ".join(f"{k}={v}" for k, v in sorted(outcomes.items())))


def calibrate(conn) -> None:
    """Replay trades live actually took and check the exit engine reproduces
    their real outcomes. Without this the arm results are unfalsifiable — an
    engine that mis-scores known trades will mis-score hypothetical ones."""
    rows = conn.execute(
        """
        SELECT t.id, t.symbol, t.direction, t.entry_price, t.open_time, t.status,
               s.stop_loss, s.take_profit
        FROM live_trades t JOIN signals s ON s.id = t.signal_id
        WHERE t.status != 'open' ORDER BY t.id
        """
    ).fetchall()
    agree = total = 0
    mism: list[str] = []
    for tid, sym, dirn, fill, open_ms, actual, sl, tp in rows:
        candles = load_candles(conn, sym, open_ms - CANDLE_MS,
                               open_ms + MAX_HOLD_MS)
        if not candles:
            continue
        r, sim = simulate_exit(float(fill), float(sl), float(tp), dirn, candles)
        got_win = sim in ("target", "partial+BE")
        real_win = actual == "target_hit"
        total += 1
        if got_win == real_win:
            agree += 1
        else:
            mism.append(f"#{tid} {sym} sim={sim} actual={actual}")
    print(f"\nCALIBRATION — exit engine vs {total} real live trades")
    print(f"  agreement: {agree}/{total} ({100*agree/max(total,1):.0f}%)")
    for m in mism[:8]:
        print(f"    mismatch: {m}")


def main(path: str) -> int:
    skips = load_skips(path)
    print(f"Parsed {len(skips)} drift-gate skips")
    print(f"Config: expiry={EXPIRY_C} candles, partial={PARTIAL_R}R x{PARTIAL_F}, "
          f"maxhold={cfg.max_trade_age_hours}h, fee={FEE*100}%, slip={SLIP*100}%")

    lim_rows: list[tuple[float, str]] = []
    mkt_rows: list[tuple[float, str]] = []
    unmatched = 0
    detail: list[tuple] = []

    with get_conn() as conn:
        calibrate(conn)
        for s in skips:
            sig = find_signal(conn, s)
            if not sig:
                unmatched += 1
                continue
            candles = load_candles(
                conn, s["symbol"], s["ts_ms"] - CANDLE_MS,
                s["ts_ms"] + MAX_HOLD_MS + EXPIRY_C * CANDLE_MS,
            )
            if not candles:
                unmatched += 1
                continue
            lr, lo = arm_limit(sig, candles)
            mr, mo = arm_market(sig, s["mark"], candles)
            lim_rows.append((lr, lo))
            mkt_rows.append((mr, mo))
            detail.append((s["symbol"], s["ts_ms"], sig["dir"],
                           s["rr_mark"], lr, lo, mr, mo))

    print(f"Matched {len(lim_rows)} to signals + candles "
          f"({unmatched} unmatched, dropped)")

    summarise("ARM A — resting limit at the zone", lim_rows)
    summarise("ARM B — market entry at the drifted mark", mkt_rows)

    lim_total, mkt_total = sum(r for r, _ in lim_rows), sum(r for r, _ in mkt_rows)
    print(f"\n  VERDICT: limit {lim_total:+.2f}R vs market {mkt_total:+.2f}R  "
          f"-> {'LIMIT' if lim_total > mkt_total else 'MARKET'} wins by "
          f"{abs(lim_total-mkt_total):.2f}R")

    # Does the gate's own R:R reading predict which arm to prefer?
    print("\n  By R:R the gate measured at the mark:")
    print(f"    {'bucket':<12} {'n':>4} {'limit R':>9} {'market R':>9}")
    buckets = [("< 0", -99, 0), ("0.0-0.6", 0, 0.6), ("0.6-0.9", 0.6, 0.9),
               ("0.9-1.2", 0.9, 1.2)]
    for label, lo_b, hi_b in buckets:
        sel = [d for d in detail if lo_b <= d[3] < hi_b]
        if sel:
            print(f"    {label:<12} {len(sel):>4} "
                  f"{sum(d[4] for d in sel):>+9.2f} {sum(d[6] for d in sel):>+9.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/skips.csv"))
