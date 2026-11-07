#!/usr/bin/env bash
# Remove the automatic session agents (keeps logs and data). The wake schedule is separate:
#   sudo pmset repeat cancel
set -euo pipefail
DOMAIN="gui/$(id -u)"
for L in com.tradeboss.session-start com.tradeboss.session-stop; do
  launchctl bootout "$DOMAIN/$L" >/dev/null 2>&1 || echo "($L was not loaded)"
  rm -f "$HOME/Library/LaunchAgents/$L.plist"
done
echo "Removed the session agents. To drop the 08:30 wake too: sudo pmset repeat cancel"
