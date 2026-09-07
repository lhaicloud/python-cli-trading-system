"""
Standalone, persistent trade watcher (runs as systemd: lqmtf-tradewatch.service).

Fills the one Telegram gap in the live app: the app already alerts on trade
OPEN and CLOSE, but partial-TP firing is only logged. This watcher polls the DB
read-only and pushes a Telegram alert the moment partial_taken flips 0->1, plus
a close summary noting whether the partial fired.

Read-only DB access (WAL-safe); never writes to the trading DB.
State persisted in data/trade_watch_state.json so restarts don't re-alert.
"""
from __future__ import annotations
import json, sqlite3, time, sys, traceback
from pathlib import Path

from app.config import get_settings
from app.notifications.telegram import send_message

settings   = get_settings()
DB_PATH    = Path(settings.db_path)
STATE_PATH = DB_PATH.parent / "trade_watch_state.json"
POLL_SECS  = 60

def ro_conn():
    c = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=10)
    c.row_factory = sqlite3.Row
    return c

def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return None

def save_state(st):
    STATE_PATH.write_text(json.dumps(st, indent=2))

def fetch(ids_gt: int):
    with ro_conn() as c:
        new = c.execute(
            "SELECT id,symbol,direction,status,partial_taken,partial_pnl,"
            "ROUND(pnl,2) pnl,risk_reward FROM paper_trades WHERE id > ? ORDER BY id",
            (ids_gt,),
        ).fetchall()
        return [dict(r) for r in new]

def fetch_ids(ids: list[int]):
    if not ids: return {}
    q = ",".join("?" * len(ids))
    with ro_conn() as c:
        rows = c.execute(
            f"SELECT id,symbol,direction,status,partial_taken,partial_pnl,"
            f"ROUND(pnl,2) pnl FROM paper_trades WHERE id IN ({q})", ids
        ).fetchall()
    return {r["id"]: dict(r) for r in rows}

def init_state():
    with ro_conn() as c:
        mx = c.execute("SELECT COALESCE(MAX(id),0) m FROM paper_trades").fetchone()["m"]
        opens = c.execute(
            "SELECT id,partial_taken FROM paper_trades WHERE status='open'"
        ).fetchall()
    watching = {str(r["id"]): {"partial_taken": r["partial_taken"], "alerted_partial": bool(r["partial_taken"])}
                for r in opens}
    return {"baseline_id": mx, "watching": watching}

def main():
    print(f"[trade-watch] starting. db={DB_PATH}", flush=True)
    st = load_state()
    if st is None:
        st = init_state()
        save_state(st)
        send_message(
            f"👀 <b>Trade-watch started</b>\nMonitoring for partial-TP fires.\n"
            f"Baseline id={st['baseline_id']}, currently watching {len(st['watching'])} open trade(s)."
        )
    print(f"[trade-watch] baseline={st['baseline_id']} watching={list(st['watching'])}", flush=True)

    while True:
        try:
            # 1. discover new trades
            new = fetch(st["baseline_id"])
            for t in new:
                st["baseline_id"] = max(st["baseline_id"], t["id"])
                st["watching"][str(t["id"])] = {
                    "partial_taken": t["partial_taken"],
                    "alerted_partial": bool(t["partial_taken"]),
                }
                print(f"[trade-watch] now tracking #{t['id']} {t['symbol']} {t['direction']}", flush=True)

            # 2. update watched trades
            ids = [int(i) for i in st["watching"]]
            cur = fetch_ids(ids)
            for sid in list(st["watching"]):
                row = cur.get(int(sid))
                w = st["watching"][sid]
                if row is None:
                    continue
                # partial-TP fired?
                if row["partial_taken"] and not w["alerted_partial"]:
                    send_message(
                        f"✅ <b>Partial-TP FIRED</b> — #{row['id']} {row['symbol']} {row['direction']}\n"
                        f"50% closed at +1.5R, partial pnl = {row['partial_pnl']}"
                    )
                    w["alerted_partial"] = True
                    print(f"[trade-watch] PARTIAL fired #{sid}", flush=True)
                # closed?
                if row["status"] != "open":
                    fired = "with partial-TP" if row["partial_taken"] else "WITHOUT partial-TP"
                    send_message(
                        f"🏁 <b>Trade #{row['id']} closed</b> ({row['status']}) {fired}\n"
                        f"{row['symbol']} {row['direction']}  pnl = {row['pnl']}"
                    )
                    print(f"[trade-watch] closed #{sid} status={row['status']}", flush=True)
                    del st["watching"][sid]

            save_state(st)
        except Exception:
            traceback.print_exc()
        time.sleep(POLL_SECS)

if __name__ == "__main__":
    main()
