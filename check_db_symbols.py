from dotenv import load_dotenv; load_dotenv()
from app.db.connection import get_conn
with get_conn() as conn:
    syms = [r[0] for r in conn.execute('SELECT DISTINCT symbol FROM candles ORDER BY symbol').fetchall()]
    print('symbols in DB:', syms)
    counts = conn.execute('SELECT symbol, COUNT(*) as n FROM candles WHERE timeframe="30m" GROUP BY symbol ORDER BY symbol').fetchall()
    print('30m candle counts:')
    for s, c in counts:
        print(f'  {s}: {c:,}')
