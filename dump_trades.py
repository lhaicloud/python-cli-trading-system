import sqlite3
conn = sqlite3.connect("data/db/lqmtf.db")
conn.row_factory = sqlite3.Row
rows = conn.execute("SELECT * FROM paper_trades ORDER BY symbol, id").fetchall()
for r in rows:
    d = dict(r)
    close = str(d.get("close_time") or "")[:16] or "OPEN"
    print(f"#{d['id']:3} {d['symbol']:<10} {d.get('direction','?'):<4} "
          f"entry={d.get('entry_price',0):.4f}  "
          f"status={str(d.get('status','?')):<12}  "
          f"pnl={d.get('pnl') or 0:+.2f}  close={close}")
conn.close()
