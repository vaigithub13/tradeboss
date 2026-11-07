#!/usr/bin/env bash
# Is the automatic session set up, and is it running now?
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../../.." && pwd)"
DOMAIN="gui/$(id -u)"
for L in com.tradeboss.session-start com.tradeboss.session-stop; do
  if launchctl print "$DOMAIN/$L" >/tmp/tb-$L.txt 2>&1; then
    echo "$L: installed ($(awk -F'= ' '/^\tstate =/ {print $2; exit}' /tmp/tb-$L.txt), last exit $(awk -F'= ' '/last exit code =/ {print $2; exit}' /tmp/tb-$L.txt))"
  else
    echo "$L: NOT INSTALLED (run $HERE/install-session.sh)"
  fi
  tail -n 3 "$REPO_ROOT/data/logs/$L.log" 2>/dev/null | sed 's/^/    /'
done
echo
echo "Wake schedule:"; pmset -g sched 2>/dev/null | sed 's/^/  /' || true
echo
if /usr/sbin/lsof -nP -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1; then echo "Backend: running on :8000"; else echo "Backend: not running"; fi
if /usr/sbin/lsof -nP -iTCP:5173 -sTCP:LISTEN >/dev/null 2>&1; then echo "Frontend: http://localhost:5173"; else echo "Frontend: not running"; fi
