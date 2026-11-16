#!/usr/bin/env bash
# Started by launchd (com.tradeboss.session-stop) at 16:35 IST: after the 15:45 reconcile and its retries
# (until 16:30). Sends ONE Ctrl+C (SIGINT) to the session backend via the ownership file (or pgrep fallback),
# as MARKET_DAY_CHECKLIST.md asks, waits for the clean shutdown (spread report, paper day file), then stops
# the frontend preview. Then the Mac may sleep again.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$(cd "$HERE/../.." && pwd)"
PY="$BACKEND/.venv/bin/python"
log() { echo "$(TZ=Asia/Kolkata date '+%F %T IST') session-stop: $*"; }

if [[ ! -x "$PY" ]]; then
  log "no venv python at $PY"
  exit 0
fi
(cd "$BACKEND" && "$PY" -m scripts.session_stop)
exit 0
