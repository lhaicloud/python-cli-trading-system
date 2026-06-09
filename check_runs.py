import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from dotenv import load_dotenv; load_dotenv()
from app.db.migrations import run_migrations; run_migrations()
from app.data.repository import get_backtest_runs
import datetime

runs = get_backtest_runs('BTCUSDT', limit=10)
print(f'Found {len(runs)} backtest runs in DB')
for r in runs:
    m = r.get('metrics', {}) or {}
    st = datetime.datetime.utcfromtimestamp(r['start_time']/1000).strftime('%Y-%m-%d')
    en = datetime.datetime.utcfromtimestamp(r['end_time']/1000).strftime('%Y-%m-%d')
    print(f"  Run#{r['id']} [{st} to {en}]: trades={m.get('total_trades',0)}, WR={m.get('win_rate',0)*100:.1f}%, PF={m.get('profit_factor',0):.3f}, profit=${m.get('net_profit',0):.2f}, DD={m.get('max_drawdown_pct',0):.1f}%")
