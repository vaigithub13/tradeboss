#!/usr/bin/env bash
# Did the daily snapshot job run? Shows launchd's view, the log, and the snapshots on disk.
set -uo pipefail

LABEL="com.tradeboss.instruments-snapshot"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../../.." && pwd)"
DOMAIN="gui/$(id -u)"

echo "== launchd =="
if launchctl print "$DOMAIN/$LABEL" >/tmp/tradeboss-launchd.txt 2>&1; then
  awk -F'\t' 'NF==2 && $2 ~ /^(state|runs|last exit code|program) =/ {print "  " $2}' /tmp/tradeboss-launchd.txt
  awk '/"Hour"|"Minute"/ {gsub(/^[ \t]+/, ""); print "  schedule " $0}' /tmp/tradeboss-launchd.txt
else
  echo "  NOT INSTALLED (run backend/scripts/launchd/install.sh)"
fi
echo
echo "== last log lines ($REPO_ROOT/data/logs/snapshot.log) =="
tail -n 8 "$REPO_ROOT/data/logs/snapshot.log" 2>/dev/null | sed 's/^/  /' || true
[[ -f "$REPO_ROOT/data/logs/snapshot.log" ]] || echo "  (no log yet: the job has not run)"
echo
echo "== snapshots on disk (data/instruments/) =="
for d in $(ls -1 "$REPO_ROOT/data/instruments" 2>/dev/null | tail -n 7); do
  if [[ -f "$REPO_ROOT/data/instruments/$d/NSE.json.gz" ]]; then echo "  $d"
  elif [[ -f "$REPO_ROOT/data/instruments/$d/UNCHANGED" ]]; then echo "  $d  (unchanged, same as $(tr -d '\n' < "$REPO_ROOT/data/instruments/$d/UNCHANGED"))"
  fi
done
TODAY="$(TZ=Asia/Kolkata date +%F)"
if [[ -f "$REPO_ROOT/data/instruments/$TODAY/NSE.json.gz" ]]; then
  echo "  -> today's ($TODAY) snapshot exists: OK"
elif [[ -f "$REPO_ROOT/data/instruments/$TODAY/UNCHANGED" ]]; then
  echo "  -> today's ($TODAY) file was unchanged from the previous snapshot: OK (not saved again)"
else
  echo "  -> no snapshot for today ($TODAY) yet"
fi
