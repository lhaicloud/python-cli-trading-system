"""
Helper: runs a single backtest and prints JSON result.
Called by ab_test.py as a subprocess so env vars take effect cleanly.

Usage: python run_single_backtest.py SYMBOL START_DATE END_DATE CAPITAL
"""
import sys
import json
import datetime

symbol   = sys.argv[1]
s        = sys.argv[2]
e        = sys.argv[3]
capital  = float(sys.argv[4])

start_ms = int(datetime.datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
end_ms   = int(datetime.datetime.strptime(e, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)

from app.backtesting.engine import run_backtest
res = run_backtest(symbol, start_ms, end_ms, capital, 1.0)
print(json.dumps(res["metrics"]))
