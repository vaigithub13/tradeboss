#!/usr/bin/env bash
# Install the automatic market-day session (launchd user agents):
#   com.tradeboss.session-start  08:40 IST Mon-Fri -> session-start.sh (npm run session, Mac kept awake)
#   com.tradeboss.session-stop   16:35 IST Mon-Fri -> session-stop.sh  (one clean Ctrl+C)
# and print the one `sudo pmset` command that wakes the Mac at 08:30 IST on weekdays.
#   backend/scripts/launchd/install-session.sh
# Remove with uninstall-session.sh. Check with check-session.sh.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$(cd "$HERE/../.." && pwd)"
REPO_ROOT="$(cd "$BACKEND/.." && pwd)"
LOG_DIR="$REPO_ROOT/data/logs"
AGENTS="$HOME/Library/LaunchAgents"
DOMAIN="gui/$(id -u)"

[[ -x "$BACKEND/.venv/bin/python" ]] || { echo "No venv at $BACKEND/.venv. Run: npm run setup" >&2; exit 1; }
NPM="$(command -v npm || true)"; UV="$(command -v uv || true)"
[[ -n "$NPM" ]] || { echo "npm not found in PATH" >&2; exit 1; }
[[ -n "$UV" ]] || { echo "uv not found in PATH" >&2; exit 1; }
# launchd starts jobs with a minimal PATH: give them the folders npm, node and uv live in.
JOB_PATH="$(dirname "$NPM"):$(dirname "$UV"):/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
chmod +x "$HERE/session-start.sh" "$HERE/session-stop.sh"

# IST times in this Mac's local time (launchd and pmset schedule in local time).
to_local() {
  /usr/bin/python3 - "$1" "$2" <<'PY'
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
h, m = int(sys.argv[1]), int(sys.argv[2])
loc = datetime.now(ZoneInfo("Asia/Kolkata")).replace(hour=h, minute=m, second=0, microsecond=0).astimezone()
print(loc.hour, loc.minute)
PY
}
read -r SH SM < <(to_local 8 40)
read -r EH EM < <(to_local 16 35)
read -r WH WM < <(to_local 8 30)

weekdays() {  # $1 hour, $2 minute -> five calendar entries, Monday..Friday
  for d in 1 2 3 4 5; do
    printf '    <dict><key>Weekday</key><integer>%s</integer><key>Hour</key><integer>%s</integer><key>Minute</key><integer>%s</integer></dict>\n' "$d" "$1" "$2"
  done
}

write_plist() {  # $1 label, $2 script, $3 hour, $4 minute, $5 keepalive xml
  cat > "$AGENTS/$1.plist" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$1</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$2</string></array>
  <key>WorkingDirectory</key><string>$REPO_ROOT</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>$JOB_PATH</string></dict>
  <key>StartCalendarInterval</key>
  <array>
$(weekdays "$3" "$4")
  </array>
$5
  <key>StandardOutPath</key><string>$LOG_DIR/$1.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/$1.log</string>
  <key>ProcessType</key><string>Interactive</string>
</dict>
</plist>
PLIST_EOF
  /usr/bin/plutil -lint "$AGENTS/$1.plist" >/dev/null
  launchctl bootout "$DOMAIN/$1" >/dev/null 2>&1 || true
  launchctl bootstrap "$DOMAIN" "$AGENTS/$1.plist"
  launchctl enable "$DOMAIN/$1"
}

mkdir -p "$LOG_DIR" "$AGENTS"
# Restart only after a crash (non-zero exit), never after a clean stop; at most once a minute.
RESTART='  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>60</integer>'
write_plist com.tradeboss.session-start "$HERE/session-start.sh" "$SH" "$SM" "$RESTART"
write_plist com.tradeboss.session-stop "$HERE/session-stop.sh" "$EH" "$EM" ""

printf 'Installed:\n  com.tradeboss.session-start  Mon-Fri 08:40 IST (%02d:%02d local)\n  com.tradeboss.session-stop   Mon-Fri 16:35 IST (%02d:%02d local)\n' "$SH" "$SM" "$EH" "$EM"
echo "  logs: $LOG_DIR/com.tradeboss.session-*.log"
echo
echo "One more step (needs your password once): wake the Mac at 08:30 IST on weekdays:"
printf '  sudo pmset repeat wakeorpoweron MTWRF %02d:%02d:00\n' "$WH" "$WM"
echo "Check everything with: $HERE/check-session.sh"
