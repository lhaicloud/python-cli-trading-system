"""Quick check on candle availability per symbol and year."""
from app.db.connection import get_conn
from datetime import datetime, timezone

SYMBOLS = ["BNBUSDT", "SOLUSDT", "BTCUSDT", "ETHUSDT"]
TFS = ["30m", "1h", "4h"]

with get_conn() as conn:
    for sym in SYMBOLS:
        print(f"\n{sym}:")
        for tf in TFS:
            row = conn.execute(
                "SELECT COUNT(*), MIN(open_time), MAX(open_time) FROM candles WHERE symbol=? AND timeframe=?",
                (sym, tf)
            ).fetchone()
            count, lo, hi = row[0], row[1], row[2]
            lo_s = datetime.fromtimestamp(lo/1000, tz=timezone.utc).date() if lo else "N/A"
            hi_s = datetime.fromtimestamp(hi/1000, tz=timezone.utc).date() if hi else "N/A"
            print(f"  {tf}: {count:,} candles  {lo_s} to {hi_s}")
