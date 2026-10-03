#!/usr/bin/env bash
# Remove the daily instrument-snapshot job. Keeps all saved snapshots and the log.
set -euo pipefail

LABEL="com.tradeboss.instruments-snapshot"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || echo "(job was not loaded)"
if [[ -f "$PLIST" ]]; then
  rm "$PLIST"
  echo "Removed $PLIST"
else
  echo "(no plist at $PLIST)"
fi
echo "Uninstalled $LABEL. Saved snapshots in data/instruments/ and data/logs/snapshot.log were left alone."
