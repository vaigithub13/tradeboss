#!/usr/bin/env bash
# Started by launchd (com.tradeboss.session-stop) at 16:35 IST: after the 15:45 reconcile and its retries
# (until 16:30). Sends ONE Ctrl+C (SIGINT) to the session backend, as MARKET_DAY_CHECKLIST.md asks, waits for the
# clean shutdown (spread report, paper day file), then stops the frontend preview. Then the Mac may sleep again.
set -uo pipefail
log() { echo "$(TZ=Asia/Kolkata date '+%F %T IST') session-stop: $*"; }

PIDS="$(/usr/bin/pgrep -f 'app\.serve app\.main:app' || true)"
if [[ -z "$PIDS" ]]; then
  log "no session backend running"
else
  log "sending one SIGINT to the backend ($PIDS)"
  kill -INT $PIDS 2>/dev/null || true
  for _ in $(seq 1 60); do
    /usr/bin/pgrep -f 'app\.serve app\.main:app' >/dev/null || break
    sleep 1
  done
  if /usr/bin/pgrep -f 'app\.serve app\.main:app' >/dev/null; then
    log "backend still running after 60 s: leaving it (check data/logs/backend-$(TZ=Asia/Kolkata date +%F).log)"
  else
    log "backend shut down cleanly"
  fi
fi
/usr/bin/pkill -INT -f 'vite preview' 2>/dev/null && log "frontend preview stopped" || true
exit 0
