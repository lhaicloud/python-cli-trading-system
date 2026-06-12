"""
One-time data repair for paper_trades + per-symbol capital settings.

Fixes:
  1. close_time stored as ISO TEXT (legacy manual-close scripts) -> epoch ms.
  2. Optionally (--reset-capital) resets every paper_capital_<sym> setting to
     default_capital + sum(closed PnL), removing drift accumulated by manual
     closes that skipped the capital update and by the (now fixed) PriceMonitor
     double-count.

IMPORTANT
  - Run only while NO live watcher is writing to the DB (run on the live host
    after stopping the runner, or against a copy).
  - Pass --apply to write; default is a dry run.

Usage (from repo root):
    python -m scripts.repair_paper_trades                 # dry run
    python -m scripts.repair_paper_trades --apply
    python -m scripts.repair_paper_trades --apply --reset-capital
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

from app.config import get_settings
from app.db.connection import get_conn


def _iso_to_ms(text: str) -> int | None:
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M UTC",   # legacy close_all.py format
        "%Y-%m-%d %H:%M",
    ):
        try:
            dt = datetime.strptime(text.strip(), fmt).replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except ValueError:
            continue
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair paper_trades data")
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry run)")
    parser.add_argument("--reset-capital", action="store_true",
                        help="Reset paper_capital_<sym> to default_capital + closed PnL")
    args = parser.parse_args()
    cfg = get_settings()

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, symbol, open_time, close_time FROM paper_trades WHERE status != 'open'"
        ).fetchall()

        # 1. TEXT close_time -> epoch ms
        fixes: list[tuple[int, int]] = []
        for r in rows:
            ct = r["close_time"]
            if ct is None:
                continue
            try:
                int(ct)
                continue  # already numeric
            except (ValueError, TypeError):
                pass
            ms = _iso_to_ms(str(ct))
            if ms is None:
                print(f"  trade #{r['id']}: unparseable close_time {ct!r} — skipped")
                continue
            fixes.append((r["id"], ms))
            print(f"  trade #{r['id']} ({r['symbol']}): close_time {ct!r} -> {ms}")

        if not fixes:
            print("No TEXT close_times found.")
        if args.apply and fixes:
            conn.executemany(
                "UPDATE paper_trades SET close_time=? WHERE id=?",
                [(ms, tid) for tid, ms in fixes],
            )
            print(f"Fixed {len(fixes)} close_time value(s).")

        # 2. Capital reconciliation
        if args.reset_capital:
            pnl_rows = conn.execute(
                "SELECT symbol, SUM(pnl) FROM paper_trades WHERE status != 'open' GROUP BY symbol"
            ).fetchall()
            for sym, pnl in pnl_rows:
                key = f"paper_capital_{sym.lower()}"
                cur = conn.execute(
                    "SELECT value FROM app_settings WHERE key=?", (key,)
                ).fetchone()
                expected = round(cfg.default_capital + (pnl or 0), 2)
                actual = float(cur[0]) if cur else None
                if actual is None or abs(actual - expected) < 0.01:
                    continue
                print(f"  {sym}: capital {actual} -> {expected} (drift {actual - expected:+.2f})")
                if args.apply:
                    conn.execute(
                        "UPDATE app_settings SET value=?, updated_at=CURRENT_TIMESTAMP WHERE key=?",
                        (str(expected), key),
                    )

    print("Applied." if args.apply else "Dry run only — re-run with --apply to write.")


if __name__ == "__main__":
    main()
