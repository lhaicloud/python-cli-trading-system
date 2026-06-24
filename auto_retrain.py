#!/usr/bin/env python3
"""Weekly self-learning: retrain + validate + activate ML models for all eligible symbols."""
import os
import sys
import sqlite3
import subprocess
from datetime import datetime, timezone

sys.path.insert(0, '/root/lqmtf')
os.chdir('/root/lqmtf')

from dotenv import load_dotenv
load_dotenv('/root/lqmtf/.env')

from app.models.trainer import train_model
from app.models.validator import validate_model, activate_model
from app.models.versioning import model_file_path
from app.notifications.telegram import send_message
from app.utils.logger import get_logger

logger = get_logger('auto_retrain')

MIN_SAMPLES = 50

db = os.environ.get('DB_PATH', 'data/db/lqmtf.db')

# ── 1. Post-promotion performance (before we retrain) ──────────────────────────
def _post_promotion_stats(conn):
    """Return stats on closed trades since the last model promotion batch."""
    cur = conn.cursor()

    # Earliest active-model promotion = start of the last retrain window
    cur.execute(
        "SELECT MIN(created_at) FROM model_versions WHERE status='active'"
    )
    row = cur.fetchone()
    last_promoted_at = row[0] if row and row[0] else None

    if not last_promoted_at:
        return None

    cur.execute(
        "SELECT COUNT(*), SUM(pnl), SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) "
        "FROM paper_trades WHERE status != 'open' AND created_at >= ?",
        (last_promoted_at,)
    )
    n, net_pnl, n_wins = cur.fetchone()
    n       = n or 0
    net_pnl = net_pnl or 0.0
    n_wins  = n_wins or 0

    return {
        'since':    last_promoted_at[:16],
        'n':        n,
        'net_pnl':  net_pnl,
        'win_rate': (n_wins / n * 100) if n > 0 else 0.0,
        'n_wins':   n_wins,
    }

conn = sqlite3.connect(db)
perf = _post_promotion_stats(conn)

# ── 2. Retrain ─────────────────────────────────────────────────────────────────
cur = conn.cursor()
cur.execute(
    "SELECT symbol FROM feature_snapshots WHERE outcome IS NOT NULL "
    "GROUP BY symbol HAVING COUNT(*) >= ?",
    (MIN_SAMPLES,)
)
symbols = [r[0] for r in cur.fetchall()]
conn.close()

logger.info("Auto-retrain: eligible symbols: %s", symbols)

results = {}
for sym in symbols:
    results[sym] = {}
    for mt in ['buy', 'sell']:
        try:
            metrics   = train_model(symbol=sym, model_type=mt)
            candidate = model_file_path(sym, mt, metrics['version'])
            report    = validate_model(sym, mt, candidate)
            if report['accepted']:
                activate_model(sym, mt, metrics['version'])
                results[sym][mt] = {'status': 'promoted', 'f1': report['candidate_f1']}
                logger.info("PROMOTED %s/%s  F1=%.3f", sym, mt, report['candidate_f1'])
            else:
                results[sym][mt] = {'status': 'rejected', 'f1': report.get('candidate_f1', 0), 'reason': report['reason']}
                logger.info("REJECTED %s/%s: %s", sym, mt, report['reason'])
        except Exception as exc:
            results[sym][mt] = {'status': 'failed', 'f1': 0, 'reason': str(exc)}
            logger.error("FAILED %s/%s: %s", sym, mt, exc)

# ── 3. Build notification ──────────────────────────────────────────────────────
n_promoted = sum(1 for s in results for m in results[s] if results[s][m]['status'] == 'promoted')
n_rejected = sum(1 for s in results for m in results[s] if results[s][m]['status'] == 'rejected')
n_failed   = sum(1 for s in results for m in results[s] if results[s][m]['status'] == 'failed')

def _cell(sym, mt):
    r = results.get(sym, {}).get(mt)
    if not r:             return '  —  '
    if r['status'] == 'promoted': return '%.3f' % r['f1']
    if r['status'] == 'rejected': return ' rej '
    return ' err '

table_rows = ['%-12s  %5s  %5s' % (sym, _cell(sym, 'buy'), _cell(sym, 'sell'))
              for sym in sorted(results)]

detail_lines = [
    '%s/%s: %s' % (sym, mt, results[sym][mt].get('reason', '')[:60])
    for sym in sorted(results)
    for mt in ['buy', 'sell']
    if results[sym].get(mt, {}).get('status') in ('failed', 'rejected')
]

now_utc     = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
status_icon = '⚠️' if n_failed > 0 else ('✅' if n_promoted > 0 else 'ℹ️')

lines = ['🤖 <b>LQ-MTF Auto-Retrain</b>', '']

# Post-promotion performance block
if perf and perf['n'] > 0:
    pnl_sign  = '+' if perf['net_pnl'] >= 0 else ''
    perf_icon = '📈' if perf['net_pnl'] >= 0 else '📉'
    health    = '✅ Effective' if perf['net_pnl'] > 0 and perf['win_rate'] >= 50 else '⚠️ Underperforming'
    lines += [
        '<b>Since last promotion</b>  <i>(%s)</i>' % perf['since'],
        '%s %s  |  %d trades  |  %.0f%% WR  |  <b>%s$%.2f</b>' % (
            perf_icon, health, perf['n'], perf['win_rate'], pnl_sign, perf['net_pnl']),
        '',
    ]
elif perf:
    lines += ['<i>No closed trades since last promotion (%s)</i>' % perf['since'], '']

# Retrain summary
lines += [
    '%s <b>%d promoted</b>  ·  %d rejected  ·  %d failed' % (status_icon, n_promoted, n_rejected, n_failed),
    '',
    '<b>Symbol          BUY   SELL</b>',
    '<code>%s</code>' % '\n'.join(table_rows),
]

if detail_lines:
    lines += ['', '⚠️ <b>Issues:</b>', '<code>%s</code>' % '\n'.join(detail_lines)]

lines += ['', '<i>%s</i>' % now_utc]

send_message('\n'.join(lines))

# ── 4. Restart if models changed ───────────────────────────────────────────────
if n_promoted > 0:
    logger.info("New models promoted — restarting lqmtf.service")
    subprocess.run(['systemctl', 'restart', 'lqmtf.service'], check=True)

logger.info("Auto-retrain done. promoted=%d rejected=%d failed=%d", n_promoted, n_rejected, n_failed)
