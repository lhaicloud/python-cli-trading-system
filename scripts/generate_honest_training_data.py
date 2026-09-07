"""
Generate ML training labels from realistic-fill backtests.

Every one of the 7,753 pre-existing feature_snapshots was produced with market
entries filling at sig.entry_price — a zone level the market usually wasn't
trading at — so they are tagged fill_model='zone' and excluded from training
(see realistic_entry_fill in config.py). This script rebuilds the labeled set
from backtests that fill at the executable price.

Non-overlapping windows only. The historical set was inflated by overlapping
runs re-labelling the same trades (hence scripts/dedup_snapshots.py); ~200
distinct honest trades per symbol beats 2,600 duplicated fictional ones.

Resumable: skips any (symbol, window) already present as a fill_model='market'
backtest_run, so it can be killed and restarted.

    DB_PATH=... REALISTIC_ENTRY_FILL=1 python -m scripts.generate_honest_training_data
    # then:
    DB_PATH=... python -m scripts.seed_training_data market
"""

from __future__ import annotations

import datetime as dt
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

from app.backtesting.engine import run_backtest      # noqa: E402
from app.config import get_settings                  # noqa: E402
from app.db.connection import get_conn               # noqa: E402
from app.db.migrations import run_migrations         # noqa: E402

SYMBOLS  = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
CAPITAL  = 10_000.0
RISK_PCT = 1.0

# 2023 onward: 3.5 years of six-month windows. Deliberately not the full 2021+
# history — each window costs ~15 min of signal generation, and recent regimes
# are the ones the model will trade. Widen if the sample proves too thin.
START = dt.date(2023, 1, 1)
END   = dt.date(2026, 7, 1)


def windows() -> list[tuple[dt.date, dt.date]]:
    out, cur = [], START
    while cur < END:
        nxt = dt.date(cur.year + (cur.month + 6 > 12),
                      (cur.month + 6 - 1) % 12 + 1, 1)
        out.append((cur, min(nxt, END)))
        cur = nxt
    return out


def to_ms(d: dt.date) -> int:
    return int(dt.datetime(d.year, d.month, d.day,
                           tzinfo=dt.timezone.utc).timestamp() * 1000)


def already_done(symbol: str, start_ms: int, end_ms: int) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM backtest_runs WHERE symbol=? AND start_time=? "
            "AND end_time=? AND fill_model='market' LIMIT 1",
            (symbol, start_ms, end_ms),
        ).fetchone()
    return row is not None


def main() -> int:
    cfg = get_settings()
    if not cfg.realistic_entry_fill:
        sys.exit("realistic_entry_fill is OFF — this would regenerate the "
                 "contaminated labels. Set REALISTIC_ENTRY_FILL=1.")
    run_migrations()

    wins = windows()
    total = len(SYMBOLS) * len(wins)
    print(f"{len(SYMBOLS)} symbols x {len(wins)} windows = {total} backtests")
    print(f"Range {START} -> {END}, fill_model=market\n")

    counts: dict[str, int] = {}
    done = 0
    for symbol in SYMBOLS:
        for w_start, w_end in wins:
            done += 1
            s_ms, e_ms = to_ms(w_start), to_ms(w_end)
            tag = f"[{done}/{total}] {symbol} {w_start}..{w_end}"
            if already_done(symbol, s_ms, e_ms):
                print(f"  {tag}: already done, skipping")
                continue
            try:
                res = run_backtest(symbol, s_ms, e_ms, CAPITAL, RISK_PCT)
                m   = res["metrics"]
                tc  = m["total_trades"]
                counts[symbol] = counts.get(symbol, 0) + tc
                print(f"  {tag}: {tc:3d} trades  WR={m['win_rate']:.0%}  "
                      f"net=${m['net_profit']:,.0f}  "
                      f"drift_skipped={m.get('drift_skipped_count', 0)}")
            except Exception as exc:
                print(f"  {tag}: ERROR — {exc}")

    print("\nHonest trades generated this run:")
    for sym in SYMBOLS:
        print(f"  {sym}: {counts.get(sym, 0)}")
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT bt.symbol, COUNT(*) FROM backtest_trades bt "
            "JOIN backtest_runs r ON r.id=bt.backtest_run_id "
            "WHERE r.fill_model='market' AND bt.features IS NOT NULL "
            "AND bt.exit_reason IN ('sl_hit','tp_hit','timeout','ratchet_sl') "
            "GROUP BY bt.symbol ORDER BY 2 DESC"
        ).fetchall()
    print("\nTotal labelable market-fill trades in DB:")
    for sym, n in rows:
        print(f"  {sym}: {n}")
    print("\nNext: python -m scripts.seed_training_data market")
    return 0


if __name__ == "__main__":
    sys.exit(main())
