# Market-day checklist

For every trading day (first used for Monday sessions). Times are IST.

## Freeze: 09:00–16:10

- Run the app with `npm run session`, never `npm run dev`. The session backend (`app/serve.py`) has no
  auto-reload and shuts down cleanly on one Ctrl+C, and the
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

- The header badge says "live". Paper: Start strategy 1 (Log XZ) and strategy 2 (Price Channel, length 20, 5m:
  the slot's default). A Start late in the day catches up from the recording, so the signals are the same.
- Paper squares off at 15:15:00 at the live bid.

## After the close

- 15:45: the reconcile runs (retried until 16:30). Check `data/live-state/reconcile.json`: today
  `intraday_reconciled`, yesterday `final`.
- After a successful reconcile the backend stores the day's option bars (nearest weekly, ATM ±5 over the day's range)
  in `data/option_history/`; the log says `option capture <day>: expiry …`. By hand:
  `cd backend && uv run python -m scripts.capture_option_day [--day YYYY-MM-DD]`.
- The spread report is in `data/spreads/reports/<day>.md`; the paper day file `data/paper/<day>.json` has `eod_check`,
  and `late_bars` / `given_up_bars` for bars a feed gap left short (rebuilt from the backfill, or given up).
- After 16:10: stop `npm run session` with ONE Ctrl+C and wait for "Application shutdown complete" (at most a few
  seconds). The shutdown writes the spread report and the paper day file. Stopping before 15:45 logs a warning:
  the reconcile, the paper end-of-day check and the full-day spread report then do not run.
- The backend log is `data/logs/backend-YYYY-MM-DD.log` (start, stop, warnings, errors, and a traceback on a crash).
- After that, `npm run dev` and code changes are allowed again.
