#!/usr/bin/env bash
# Started by launchd (com.tradeboss.session-start) at 08:40 IST on weekdays, or on wake if the Mac slept through it.
# Starts `npm run session` (backend without auto-reload + production frontend) and keeps the Mac awake while it runs.
#
# Does nothing (exit 0) when:
#   * today is not a session day (weekend / NSE holiday; Muhurat counts as a session day),
#   * it is outside 08:00-15:30 IST (never start or restart a session late in the day),
#   * a backend is already listening on :8000 (you started it by hand: never run two).
# A crash exits non-zero, so launchd starts it again (at most once a minute) while the window is open.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$(cd "$HERE/../.." && pwd)"
REPO_ROOT="$(cd "$BACKEND/.." && pwd)"
PY="$BACKEND/.venv/bin/python"
log() { echo "$(TZ=Asia/Kolkata date '+%F %T IST') session-start: $*"; }

NOW_MIN=$((10#$(TZ=Asia/Kolkata date +%H) * 60 + 10#$(TZ=Asia/Kolkata date +%M)))
if (( NOW_MIN < 8 * 60 || NOW_MIN > 15 * 60 + 30 )); then
  log "outside 08:00-15:30 IST: not starting"
  exit 0
fi

if ! (cd "$BACKEND" && "$PY" -c "
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
from app.live.session_days import listed_session
sys.exit(0 if listed_session(datetime.now(ZoneInfo('Asia/Kolkata')).date()) else 1)
"); then
  log "not a session day: not starting"
  exit 0
fi

if /usr/sbin/lsof -nP -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1; then
  log "a backend is already listening on :8000: leaving it alone"
  exit 0
fi

log "starting npm run session (Mac kept awake while it runs)"
cd "$REPO_ROOT"
# -i: no idle sleep, -m: no disk sleep, -s: no system sleep on AC power. The display may still sleep.
exec /usr/bin/caffeinate -ims npm run session
