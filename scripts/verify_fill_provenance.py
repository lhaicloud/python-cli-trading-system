"""
Verify fill-model provenance separates honest training labels from contaminated
ones. Run against a COPY of the real DB so the migration's backfill is exercised
on actual rows:

    DB_PATH=data/db/prov_check.db python -m scripts.verify_fill_provenance
"""

from __future__ import annotations

import os
import sys

if "DB_PATH" not in os.environ:
    sys.exit("Refusing to run: set DB_PATH to a copy of the DB first.")

from app.config import get_settings                          # noqa: E402
from app.data.repository import (                             # noqa: E402
    current_fill_model,
    get_feature_snapshots,
    save_feature_snapshot,
)
from app.db.connection import get_conn                        # noqa: E402
from app.db.migrations import run_migrations                  # noqa: E402

FAILURES: list[str] = []


def check(label: str, got, want) -> None:
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: got {got!r}, want {want!r}")
    if not ok:
        FAILURES.append(label)


def main() -> int:
    cfg = get_settings()
    run_migrations()

    print("\n1. migration backfills existing rows as 'zone'")
    with get_conn() as conn:
        rows = dict(conn.execute(
            "SELECT COALESCE(fill_model,'NULL'), COUNT(*) "
            "FROM feature_snapshots GROUP BY 1"
        ).fetchall())
        runs = dict(conn.execute(
            "SELECT COALESCE(fill_model,'NULL'), COUNT(*) "
            "FROM backtest_runs GROUP BY 1"
        ).fetchall())
    print(f"     feature_snapshots by fill_model: {rows}")
    print(f"     backtest_runs by fill_model:     {runs}")
    check("no NULL fill_model left in feature_snapshots", rows.get("NULL", 0), 0)
    check("no NULL fill_model left in backtest_runs", runs.get("NULL", 0), 0)
    check("pre-existing snapshots are all 'zone'",
          rows.get("zone", 0) > 0 and set(rows) == {"zone"}, True)

    print(f"\n2. current_fill_model reflects config "
          f"(realistic_entry_fill={cfg.realistic_entry_fill})")
    check("current_fill_model", current_fill_model(),
          "market" if cfg.realistic_entry_fill else "zone")

    print("\n3. training reads exclude contaminated labels")
    # Pick a symbol that has plenty of legacy rows.
    with get_conn() as conn:
        sym = conn.execute(
            "SELECT symbol FROM feature_snapshots WHERE outcome IS NOT NULL "
            "GROUP BY symbol ORDER BY COUNT(*) DESC LIMIT 1"
        ).fetchone()[0]
    legacy_all  = len(get_feature_snapshots(sym, fill_model=None))
    honest_only = len(get_feature_snapshots(sym))
    print(f"     {sym}: {legacy_all} total rows, {honest_only} honest")
    check(f"{sym} has legacy rows", legacy_all > 0, True)
    check("default (training) read sees zero contaminated rows", honest_only, 0)

    print("\n4. a new snapshot is stamped with the live fill model")
    save_feature_snapshot({
        "signal_id": None, "symbol": "PROVTEST", "timestamp": 1,
        "features": {"x": 1.0}, "outcome": "win", "pnl": 1.0,
    })
    stamped = get_feature_snapshots("PROVTEST")
    check("new row is visible to training reads", len(stamped), 1)
    if stamped:
        check("new row fill_model", stamped[0]["fill_model"], current_fill_model())
    # A second migration pass must not retag it.
    run_migrations()
    still = get_feature_snapshots("PROVTEST")
    check("re-running migrations does not retag honest rows", len(still), 1)

    print("\n" + ("ALL CHECKS PASSED" if not FAILURES
                  else f"{len(FAILURES)} FAILURE(S): {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
