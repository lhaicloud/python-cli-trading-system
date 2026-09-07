"""Quick health report — run on the live host after deploy.

    cd ~/lqmtf && ./venv/bin/python -m scripts.goal_health_report
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from app.config import get_settings
from app.validation import passes_backtest_validation
from app.version import ENGINE_VERSION

DB = "/root/lqmtf/data/db/lqmtf.db"


def _ts(ms):
    if not ms:
        return "—"
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def main():
    cfg = get_settings()
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    print("=" * 60)
    print("LQ-MTF GOAL HEALTH REPORT")
    print("=" * 60)
    print(f"Engine:     {ENGINE_VERSION}")
    print(f"Validation: strict WR>={cfg.backtest_min_win_rate:.0%} trades>={cfg.backtest_min_trades} "
          f"profit>=${cfg.backtest_min_profit:.0f}")
    if cfg.backtest_expectancy_enabled:
        print(f"            expectancy WR>={cfg.backtest_expectancy_min_win_rate:.0%} "
              f"trades>={cfg.backtest_expectancy_min_trades} profit>=${cfg.backtest_expectancy_min_profit:.0f} "
              f"PF>={cfg.backtest_expectancy_min_pf:.2f}")
    print(f"Excluded:   {len(cfg.excluded_symbol_set)} symbols")
    print(f"Pad unval:  {cfg.pool_pad_unvalidated}")
    print()

    # Portfolio
    c.execute("SELECT value FROM app_settings WHERE key='paper_capital_portfolio'")
    row = c.fetchone()
    print(f"Paper equity: ${float(row['value']):,.2f}" if row else "Paper equity: —")

    # Live summary
    for tbl, label in [("paper_trades", "PAPER"), ("live_trades", "LIVE")]:
        c.execute(
            f"""
            SELECT COUNT(*) n,
              SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) open_n,
              SUM(CASE WHEN status!='open' AND pnl>0 THEN 1 ELSE 0 END) wins,
              SUM(CASE WHEN status!='open' THEN 1 ELSE 0 END) closed,
              ROUND(SUM(CASE WHEN status!='open' THEN pnl ELSE 0 END), 2) pnl
            FROM {tbl}
            """
        )
        r = c.fetchone()
        wr = round(r["wins"] / r["closed"] * 100, 1) if r["closed"] else 0
        print(f"{label:6} total={r['n']}  open={r['open_n']}  "
              f"closed={r['closed']}  WR={wr}%  PnL=${r['pnl'] or 0}")

    # Current engine era: exact tag or pool trades since v5 went live (ML used to
    # overwrite model_version with the sklearn dump name).
    v5_live_ms = int(datetime(2026, 8, 24, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)

    with open("coin_pool.json", encoding="utf-8-sig") as f:
        pool = json.load(f)["pool"]
    pool_ph = ",".join("?" * len(pool))

    print()
    print(f"--- {ENGINE_VERSION} era (pool, since 2026-08-24) ---")
    for tbl, label in [("paper_trades", "paper"), ("live_trades", "live")]:
        c.execute(
            f"""
            SELECT COUNT(*) n,
              SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) wins,
              ROUND(SUM(pnl),2) pnl
            FROM {tbl}
            WHERE status!='open'
              AND symbol IN ({pool_ph})
              AND open_time >= ?
            """,
            (*pool, v5_live_ms),
        )
        r = c.fetchone()
        wr = round(r["wins"] / r["n"] * 100, 1) if r["n"] else 0
        print(f"  {label}: {r['n']} trades  WR={wr}%  PnL=${r['pnl'] or 0}")

    # Open positions
    print()
    print("--- Open positions ---")
    mark_cache: dict[str, float] = {}
    for tbl, label in [("live_trades", "LIVE"), ("paper_trades", "PAPER")]:
        c.execute(
            f"""
            SELECT symbol, direction, entry_price, stop_loss, model_version,
              datetime(open_time/1000,'unixepoch') opened
            FROM {tbl} WHERE status='open'
            """
        )
        rows = c.fetchall()
        if rows:
            print(f"  {label}:")
            for r in rows:
                sl_note = ""
                if tbl == "live_trades" and r["stop_loss"]:
                    sym = r["symbol"]
                    if sym not in mark_cache:
                        try:
                            import httpx
                            resp = httpx.get(
                                "https://fapi.binance.com/fapi/v1/premiumIndex",
                                params={"symbol": sym},
                                timeout=5,
                            )
                            mark_cache[sym] = float(resp.json().get("markPrice") or 0)
                        except Exception:
                            mark_cache[sym] = 0.0
                    mark = mark_cache[sym]
                    if mark:
                        gap = (mark - r["stop_loss"]) / r["stop_loss"] * 100
                        sl_note = f"  mark={mark:.3f} SL={r['stop_loss']} ({gap:+.2f}% above SL, triggers on mark)"
                print(
                    f"    {r['symbol']} {r['direction']} @ {r['entry_price']}  "
                    f"({r['opened']}){sl_note}"
                )
        else:
            print(f"  {label}: none")

    # Pool validation
    print()
    print(f"--- Pool validation ({len(pool)} coins) ---")
    validated = []
    for sym in pool:
        c.execute(
            """
            SELECT win_rate, total_trades, net_profit, profit_factor
            FROM backtest_runs
            WHERE symbol=? AND model_version=?
            ORDER BY created_at DESC LIMIT 1
            """,
            (sym, ENGINE_VERSION),
        )
        r = c.fetchone()
        if not r:
            print(f"  {sym}: no backtest")
            continue
        ok, tier = passes_backtest_validation(
            cfg,
            net_profit=r["net_profit"] or 0,
            win_rate=r["win_rate"] or 0,
            total_trades=r["total_trades"] or 0,
            profit_factor=r["profit_factor"] or 0,
        )
        if ok:
            validated.append(sym)
        mark = f"OK ({tier})" if ok else "FAIL"
        print(f"  {mark} {sym}: WR={r['win_rate']*100:.0f}%  "
              f"n={r['total_trades']}  net=${r['net_profit']:.0f}")
    print(f"\nValidated: {len(validated)}/{len(pool)} -> {validated}")

    near = []
    for sym in pool:
        c.execute(
            """
            SELECT win_rate, total_trades, net_profit
            FROM backtest_runs
            WHERE symbol=? AND model_version=?
            ORDER BY created_at DESC LIMIT 1
            """,
            (sym, ENGINE_VERSION),
        )
        r = c.fetchone()
        if not r:
            continue
        net, wr, n = r["net_profit"] or 0, r["win_rate"] or 0, r["total_trades"] or 0
        if (
            net > cfg.backtest_min_profit
            and n >= cfg.backtest_min_trades
            and wr < cfg.backtest_min_win_rate
        ):
            gap = (cfg.backtest_min_win_rate - wr) * 100
            near.append((sym, wr, n, net, gap))
    if near:
        print()
        print("--- Near-miss (profit+trades pass, WR short) ---")
        for sym, wr, n, net, gap in sorted(near, key=lambda x: x[4]):
            print(f"  {sym}: WR={wr*100:.0f}% (need {cfg.backtest_min_win_rate*100:.0f}%, "
                  f"gap={gap:.0f}pp)  n={n}  net=${net:.0f}")

    # Recent engine runs (includes coins removed from pool after failing)
    c.execute(
        """
        SELECT symbol, win_rate, total_trades, net_profit, profit_factor, created_at as ran
        FROM backtest_runs
        WHERE model_version=?
        ORDER BY created_at DESC
        LIMIT 10
        """,
        (ENGINE_VERSION,),
    )
    recent = c.fetchall()
    if recent:
        print()
        print(f"--- Recent {ENGINE_VERSION} backtests ---")
        for r in recent:
            ok, tier = passes_backtest_validation(
                cfg,
                net_profit=r["net_profit"] or 0,
                win_rate=r["win_rate"] or 0,
                total_trades=r["total_trades"] or 0,
                profit_factor=r["profit_factor"] or 0,
            )
            mark = f"OK ({tier})" if ok else "FAIL"
            in_pool = "in pool" if r["symbol"] in pool else "removed"
            print(f"  {mark} {r['symbol']} ({in_pool}): WR={r['win_rate']*100:.0f}%  "
                  f"n={r['total_trades']}  net=${r['net_profit']:.0f}  [{r['ran']}]")

    print()
    print("--- Rotation watchlist (validated-only, top 5) ---")
    try:
        from scan_universe import select_watchlist
        picks = select_watchlist(n=5, validated_only=True, pool=pool, verbose=False)
        if picks:
            print(f"  {picks}")
        else:
            print("  (empty — no validated coins yet)")
    except Exception as exc:
        print(f"  error: {exc}")

    # v6 simple engine comparison (when backtests exist)
    v6 = "rule_based_v6_simple"
    c.execute(
        "SELECT COUNT(*) FROM backtest_runs WHERE model_version=?",
        (v6,),
    )
    if c.fetchone()[0]:
        print()
        print(f"--- {v6} backtests (Option B) ---")
        for sym in pool + ["BTCUSDT"]:
            c.execute(
                """
                SELECT win_rate, total_trades, net_profit, profit_factor, created_at
                FROM backtest_runs
                WHERE symbol=? AND model_version=?
                ORDER BY created_at DESC LIMIT 1
                """,
                (sym, v6),
            )
            r = c.fetchone()
            if not r:
                continue
            ok, tier = passes_backtest_validation(
                cfg,
                net_profit=r["net_profit"] or 0,
                win_rate=r["win_rate"] or 0,
                total_trades=r["total_trades"] or 0,
                profit_factor=r["profit_factor"] or 0,
            )
            mark = f"OK ({tier})" if ok else "FAIL"
            print(
                f"  {mark} {sym}: WR={r['win_rate']*100:.0f}%  "
                f"n={r['total_trades']}  net=${r['net_profit']:.0f}  [{r['created_at']}]"
            )
        # Side-by-side vs v5 for pool coins
        print()
        print("--- v5 vs v6 (pool coins) ---")
        comparisons: list[tuple[str, dict | None, dict | None]] = []
        for sym in pool:
            parts = []
            row_v5 = row_v6 = None
            for ver, label in [(ENGINE_VERSION, "v5"), (v6, "v6")]:
                c.execute(
                    """
                    SELECT win_rate, total_trades, net_profit, profit_factor
                    FROM backtest_runs
                    WHERE symbol=? AND model_version=?
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (sym, ver),
                )
                r = c.fetchone()
                if r:
                    row = {
                        "win_rate": r["win_rate"] or 0,
                        "total_trades": r["total_trades"] or 0,
                        "net_profit": r["net_profit"] or 0,
                        "profit_factor": r["profit_factor"] or 0,
                    }
                    if label == "v5":
                        row_v5 = row
                    else:
                        row_v6 = row
                    parts.append(
                        f"{label}: n={row['total_trades']} WR={row['win_rate']*100:.0f}% "
                        f"${row['net_profit']:.0f}"
                    )
                else:
                    parts.append(f"{label}: pending")
            comparisons.append((sym, row_v5, row_v6))
            print(f"  {sym}: {'  |  '.join(parts)}")

        complete = [(s, v5, v6) for s, v5, v6 in comparisons if v5 and v6]
        pending = [s for s, v5, v6 in comparisons if v5 and not v6]
        if complete or pending:
            print()
            print("--- v6 go/no-go ---")
            if pending:
                print(f"  Pending v6: {', '.join(pending)}")
            v5_pool = sum(v5["net_profit"] for _, v5, _ in comparisons if v5)
            v6_done = sum(v6["net_profit"] for _, _, v6 in comparisons if v6)
            v5_pending = sum(
                v5["net_profit"] for _, v5, v6 in comparisons if v5 and not v6
            )
            if pending and v5_pool:
                v6_best = v6_done + v5_pending  # optimistic: pending v6 matches v5
                if v6_best < v5_pool * 0.85:
                    print(
                        f"  Projected pool net (best-case v6): "
                        f"v5=${v5_pool:.0f}  v6<=${v6_best:.0f}  -> STAY on v5"
                    )
            if complete:
                v5_net = sum(v5["net_profit"] for _, v5, _ in complete)
                v6_net = sum(v6["net_profit"] for _, _, v6 in complete)
                v6_sparse = any(
                    v6["total_trades"] < max(8, v5["total_trades"] * 0.5)
                    for _, v5, v6 in complete
                )
                v6_valid = all(
                    passes_backtest_validation(
                        cfg,
                        net_profit=v6["net_profit"],
                        win_rate=v6["win_rate"],
                        total_trades=v6["total_trades"],
                        profit_factor=v6["profit_factor"],
                    )[0]
                    for _, _, v6 in complete
                )
                if len(complete) < len(pool):
                    worse = [
                        s for s, v5, v6 in complete
                        if v6["net_profit"] < v5["net_profit"] * 0.6
                    ]
                    if worse:
                        print(f"  Early read: STAY on v5 ({', '.join(worse)} v6 profit << v5)")
                    else:
                        print("  Early read: wait for remaining coins before switching")
                elif v6_net > v5_net and v6_valid and not v6_sparse:
                    print(
                        f"  RECOMMEND: SWITCH to v6 "
                        f"(pool net v5=${v5_net:.0f} v6=${v6_net:.0f})"
                    )
                    print("  Action: SIMPLIFIED_STRATEGY=1 in .env, restart lqmtf + lqmtf-live")
                else:
                    reasons = []
                    if v6_net <= v5_net:
                        reasons.append(f"v6 net ${v6_net:.0f} <= v5 ${v5_net:.0f}")
                    if not v6_valid:
                        reasons.append("v6 fails validation tier")
                    if v6_sparse:
                        reasons.append("v6 too sparse vs v5 trade count")
                    print(f"  RECOMMEND: STAY on v5 ({'; '.join(reasons)})")

    conn.close()


if __name__ == "__main__":
    main()
