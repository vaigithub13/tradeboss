#!/usr/bin/env bash
# Install the daily instrument-snapshot job (launchd user agent, runs at 08:30 IST).
#   backend/scripts/launchd/install.sh            install / reinstall
#   backend/scripts/launchd/install.sh --run-now  ... and run it once right away
# Remove it again with uninstall.sh; check it with check.sh.
set -euo pipefail

LABEL="com.tradeboss.instruments-snapshot"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$(cd "$HERE/../.." && pwd)"
REPO_ROOT="$(cd "$BACKEND/.." && pwd)"
PY="$BACKEND/.venv/bin/python"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$REPO_ROOT/data/logs"
RUN_NOW=0
[[ "${1:-}" == "--run-now" ]] && RUN_NOW=1

if [[ ! -x "$PY" ]]; then
  echo "Python venv not found at $PY. Run:  npm run setup   (from $REPO_ROOT)" >&2
  exit 1
fi

# 08:30 IST expressed in this Mac's local time (launchd schedules in local time).
read -r HOUR MINUTE LOCAL_TZ < <(/usr/bin/python3 - <<'PY'
from datetime import datetime
from zoneinfo import ZoneInfo
ist = datetime.now(ZoneInfo("Asia/Kolkata")).replace(hour=8, minute=30, second=0, microsecond=0)
loc = ist.astimezone()
print(loc.hour, loc.minute, loc.tzname())
PY
)
if [[ "$HOUR:$MINUTE" != "8:30" ]]; then
  echo "Note: this Mac's timezone is $LOCAL_TZ, so 08:30 IST is scheduled as $(printf '%02d:%02d' "$HOUR" "$MINUTE") local time."
  echo "      (If your timezone observes daylight saving, re-run this script after the clocks change.)"
fi

mkdir -p "$LOG_DIR" "$(dirname "$PLIST")"
cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>-m</string>
    <string>scripts.snapshot_instruments</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$BACKEND</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>$HOUR</integer>
    <key>Minute</key>
    <integer>$MINUTE</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>$LOG_DIR/snapshot.log</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/snapshot.log</string>
  <key>ProcessType</key>
  <string>Background</string>
</dict>
</plist>
PLIST_EOF
/usr/bin/plutil -lint "$PLIST" >/dev/null

DOMAIN="gui/$(id -u)"
launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true   # reinstall cleanly
launchctl bootstrap "$DOMAIN" "$PLIST"
launchctl enable "$DOMAIN/$LABEL"

echo "Installed $LABEL"
echo "  plist:    $PLIST"
echo "  schedule: daily 08:30 IST ($(printf '%02d:%02d' "$HOUR" "$MINUTE") local); a Mac that is asleep runs it on wake"
echo "  log:      $LOG_DIR/snapshot.log"
echo "  check:    $HERE/check.sh"

if [[ "$RUN_NOW" == "1" ]]; then
  launchctl kickstart "$DOMAIN/$LABEL"
  echo "Started one run now; see the log in a few seconds."
fi
