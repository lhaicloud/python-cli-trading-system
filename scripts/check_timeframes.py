"""Check which timeframes each symbol has in the DB."""
from dotenv import load_dotenv; load_dotenv()
from app.db.connection import get_conn

SYMBOLS = ["AAVEUSDT","ADAUSDT","DOGEUSDT","OPUSDT","TIAUSDT","TRXUSDT","UNIUSDT"]
TFS     = ["30m","1h","4h","12h","1d","1w"]

with get_conn() as conn:
    print(f"{'Symbol':<12}", end="")
    for tf in TFS: print(f"  {tf:>5}", end="")
    print()
    print("-" * (12 + len(TFS)*7))
    for sym in SYMBOLS:
        print(f"{sym:<12}", end="")
        for tf in TFS:
            n = conn.execute(
                "SELECT COUNT(*) FROM candles WHERE symbol=? AND timeframe=?", (sym, tf)
            ).fetchone()[0]
            print(f"  {n:>5}" if n else f"  {'—':>5}", end="")
        print()
