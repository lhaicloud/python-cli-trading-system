#!/bin/bash
# Wait for revalidate_pool batch to finish, then restart trading services.
LOG="${1:-/tmp/revalidate_v5_universe3.log}"
MARKER="/tmp/batch_watcher_$(basename "$LOG").done"

if [ -f "$MARKER" ]; then
  echo "Watcher already completed for $LOG"
  exit 0
fi

echo "$(date -u) watching for batch completion (log=$LOG)"
while pgrep -f "revalidate_pool.*--run" >/dev/null 2>&1; do
  sleep 120
done

sleep 5
echo "$(date -u) batch complete — promoting winners" | tee -a "$MARKER"
cd /root/lqmtf && ./venv/bin/python -m scripts.promote_batch_winners "$LOG" | tee -a "$MARKER"
echo "$(date -u) batch complete — denylisting losers" | tee -a "$MARKER"
cd /root/lqmtf && ./venv/bin/python -m scripts.denylist_batch_losers "$LOG" | tee -a "$MARKER"
echo "$(date -u) batch complete — restarting services" | tee -a "$MARKER"
grep -E "Promoted|trades=" "$LOG" | tail -20 | tee -a "$MARKER"
systemctl restart lqmtf lqmtf-live
echo "$(date -u) services restarted" | tee -a "$MARKER"
