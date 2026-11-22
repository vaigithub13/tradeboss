#!/usr/bin/env bash
# Saturday 10:00 IST: recompute the stock-scanner ranking. Does not start uvicorn.
#   backend/scripts/launchd/install-scanner-rank.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$(cd "$HERE/../.." && pwd)"
REPO_ROOT="$(cd "$BACKEND/.." && pwd)"
LOG_DIR="$REPO_ROOT/data/logs"
AGENTS="$HOME/Library/LaunchAgents"
DOMAIN="gui/$(id -u)"
LABEL="com.tradeboss.scanner-rank"
PY="$BACKEND/.venv/bin/python"

[[ -x "$PY" ]] || { echo "No venv at $BACKEND/.venv. Run: npm run setup" >&2; exit 1; }

read -r HH MM < <(/usr/bin/python3 - <<'PY'
from datetime import datetime
from zoneinfo import ZoneInfo
loc = datetime.now(ZoneInfo("Asia/Kolkata")).replace(hour=10, minute=0, second=0, microsecond=0).astimezone()
print(loc.hour, loc.minute)
PY
)

mkdir -p "$LOG_DIR" "$AGENTS"
cat > "$AGENTS/$LABEL.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>-m</string>
    <string>scripts.scanner_rank</string>
  </array>
  <key>WorkingDirectory</key><string>$BACKEND</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Weekday</key><integer>6</integer>
    <key>Hour</key><integer>$HH</integer>
    <key>Minute</key><integer>$MM</integer>
  </dict>
  <key>StandardOutPath</key><string>$LOG_DIR/$LABEL.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/$LABEL.log</string>
</dict>
</plist>
EOF
/usr/bin/plutil -lint "$AGENTS/$LABEL.plist" >/dev/null
launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
launchctl bootstrap "$DOMAIN" "$AGENTS/$LABEL.plist"
launchctl enable "$DOMAIN/$LABEL"
printf 'Installed %s Saturday 10:00 IST (%02d:%02d local)\n' "$LABEL" "$HH" "$MM"
