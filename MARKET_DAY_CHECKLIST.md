# Market-day checklist

For every trading day (first used for Monday sessions). Times are IST.

## Freeze: 09:00–16:10

- Run the app with `npm run session`, never `npm run dev`. The session backend has no auto-reload, and the
  frontend is a production build served by `vite preview` (same `/api` and `/ws` proxy, no hot reload).
- Do not edit code in this repo, and do not start a second backend, a demo backend or a replay script
  that opens the feed. A reload in the middle of the day restarts the live engine and the paper session.
  On 6 Oct 2026 a reload between 15:30 and 15:45 dropped the day's close log and its end-of-day check.
- Read-only work (reading files, `git log`, looking at `data/`) is fine.

## Before 08:55

1. Instrument snapshot: `backend/scripts/launchd/check.sh` should say today's snapshot exists or is unchanged.
   The chart header shows an amber "snapshot" badge when the newest snapshot is older than one trading day.
2. `.env`: `SPREAD_RECORDER_ENABLED=true` if spreads are being recorded.
3. Stop any `npm run dev` and leftover backends: `lsof -nP -iTCP:8000 -iTCP:8001 -sTCP:LISTEN` should be empty.
4. `npm run session`. The feed connects from 08:55 (`LIVE_CONNECT_START`).

## During the session

- The header badge says "live". Paper: pick the strategy and Start (a Start late in the day catches up from
  the recording, so the signals are the same).
- Paper squares off at 15:15:00 at the live bid.

## After the close

- 15:45: the reconcile runs (retried until 16:30). Check `data/live-state/reconcile.json`: today
  `intraday_reconciled`, yesterday `final`.
- The spread report is in `data/spreads/reports/<day>.md`; the paper day file `data/paper/<day>.json` has `eod_check`.
- After 16:10: stop `npm run session` (Ctrl-C). `npm run dev` and code changes are allowed again.
