#!/bin/bash
# Watch v6 revalidate_pool PID; write summary when done.
PID="${1:-24850}"
OUT="/tmp/v6_done_summary.txt"
LOG="/tmp/revalidate_v6_simple.log"

echo "Watching PID $PID for v6 completion..." | tee "$OUT"
while kill -0 "$PID" 2>/dev/null; do
  sleep 60
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) still running ($(ps -p $PID -o etime= 2>/dev/null || echo dead))" >> "$OUT"
done

echo "" >> "$OUT"
echo "=== v6 log results ===" >> "$OUT"
grep -E 'trades=|=== |Revalidation pass' "$LOG" >> "$OUT" 2>/dev/null || true

echo "" >> "$OUT"
echo "=== health report (tail) ===" >> "$OUT"
cd ~/lqmtf && ./venv/bin/python -m scripts.goal_health_report 2>&1 | tail -25 >> "$OUT"

echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) v6 watcher done" >> "$OUT"
