"""Check existing candle data in DB."""
from app.db.migrations import run_migrations
run_migrations()
from app.data.repository import count_candles, get_earliest_candle_time, get_latest_candle_time
import datetime

for tf in ["1d", "4h", "1h", "30m"]:
    cnt = count_candles("BTCUSDT", tf)
    earliest = get_earliest_candle_time("BTCUSDT", tf)
    latest = get_latest_candle_time("BTCUSDT", tf)
    e_dt = datetime.datetime.utcfromtimestamp(earliest/1000).strftime("%Y-%m-%d") if earliest else "N/A"
    l_dt = datetime.datetime.utcfromtimestamp(latest/1000).strftime("%Y-%m-%d") if latest else "N/A"
    print(f"{tf}: {cnt} candles  [{e_dt} to {l_dt}]")
