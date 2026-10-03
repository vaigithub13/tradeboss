# Progress

## Phase 0 — Skeleton ✅

- Folder structure per PROJECT_PLAN.md, FastAPI app (`GET /api/health`), Vite + React 19 + strict TS + Tailwind v4 (dark) + Zustand, `.env.example`, root `package.json` + `Makefile`.
- One command to start both: `npm run dev` (or `make dev`). Frontend shows "backend connected".

## Phase 1a — Chart core with sample data ✅

**Sample data** (imported read-only from `My Trading Desk`, which was not modified)
- Only **5m** Nifty 50 history exists there (no 1m). Two Upstox datasets merged: 2022-01-03 → 2026-09-30.
- 88,093 raw bars → 87,915 kept → `data/candles/NIFTY50/5m.parquet` (gitignored), read via DuckDB. Index volume is 0 everywhere.
- Re-create with: `cd backend && uv run python -m scripts.import_nifty_sample`
- Cleaning (`app/data/importer.py`): bars must START in 09:15–15:30 IST, except Muhurat sessions which are kept whole. Dropped 178 stray prints (84× 09:10 pre-open, 84× 15:30, 5× 15:35, 5× 15:55).
- No missing 5m bars inside normal sessions; 67 weekdays have no data (holidays).

**Session labels** (`app/data/sessions.py`, stored per bar in the Parquet `session_type` column)
- Labels are by session TYPE, not weekday (follow-up A, applied after 1a approval):
  - `normal` — full-length Mon–Fri session (included by default)
  - `weekend_full` — FULL-length Saturday/Sunday session, e.g. 2024-01-20, 2025-02-01, 2026-02-01 (included by default)
  - `special_short` — short/broken session on any day, e.g. 2024-03-02, 2024-05-18 (excluded by default)
  - `muhurat` — Diwali Muhurat, any shape, from the calendar list (excluded by default)
  - Precedence: muhurat > not-full-length → special_short > weekend → weekend_full > normal. "Full-length" = first bar ≤ 09:15, last bar ends ≥ 15:30, ≥ 90% of expected bars.
- Muhurat dates are a hard-coded list `MUHURAT_DATES` (2022-10-24, 2023-11-12 [a Sunday], 2024-11-01, 2025-10-21) — **to be replaced in Phase 2 (see below)**.
- Sample data counts: 1,168 normal sessions · 3 weekend_full · 2 special_short · 4 muhurat.
- Which types are shown: `INCLUDE_SESSION_TYPES` in `.env` (default `normal,weekend_full`); API param `sessions=normal,muhurat,…` overrides; UI has a "Sessions" menu (regular sessions are always on).
- Muhurat days (when included) bypass the 09:15–15:30 clip and anchor intraday candles to their own first bar (they run 18:00 or 13:45, not 09:15). special_short sessions keep the 09:15 anchor.

**Resampler** (`app/data/resampler.py`, tests written first and approved)
- 1m/3m/5m/15m/30m/1h/1D/1W, IST, anchored to 09:15 (1h = 09:15…15:15–15:30 partial), weeks Mon–Sun starting on the first trading day, no fake candles, OHLCV rules.
- Sunday Budget session 2026-02-01 joins the week of Mon 2026-01-26 (**rule pending your TradingView check**).
- A timeframe finer than the stored data raises `ValueError`; 1m/3m logic + tests are kept for when 1m data exists.

**API**
- `GET /api/candles?symbol=&timeframe=&from=&to=[&sessions=]` — `from`/`to` are unix seconds (inclusive, on candle START time); source bars are loaded for whole IST days/weeks around the range so edge candles are complete. 404 unknown symbol, 422 unknown/unavailable timeframe (message says what the stored data is).
- `GET /api/symbols` — symbols, base timeframe, available timeframes, data range, default session types.
- Gzip enabled (5m full history ≈ 9 MB raw).

**Frontend**
- Lightweight Charts v5 candlestick chart, dark theme, full screen; wheel zoom, drag pan; crosshair + OHLC/change legend top-left; time axis and crosshair label in IST (`Intl`, Asia/Kolkata).
- Timeframe buttons 1m 3m 5m 15m 30m 1h 1D 1W. **1m/3m disabled with tooltip** ("Needs 1m data … available after Phase 2") — never fabricated.
- Volume pane appears only if the loaded range has volume > 0 (hidden for index data).
- Zustand `chartStore` (session-type selection included) with stale-response protection; default timeframe 15m, last 150 bars visible initially.

**Tests** at the end of 1a: pytest 86 · Vitest 34 (current totals are in the Phase 1b section). `npm run typecheck` clean, `vite build` OK.

**Visually checked in a browser:** chart renders, 15m/1D switching, crosshair + IST date label, legend, disabled 1m/3m, Muhurat toggle, volume pane (using a temporary symbol with volume, since deleted). Not exercised in a browser: removing the volume pane when switching from a symbol with volume to one without (no symbol selector yet; Phase 2).

Known / deferred (by design):
- (Lazy loading / 100k+ performance were done in Phase 1c below.)
- `npm audit` flags a moderate dev-only issue in Vitest's mocker (fix needs a major Vitest bump); not touched.

Run:
```
npm install && npm run setup   # first time only
npm run dev                    # backend :8000 + frontend :5173 -> open http://localhost:5173
npm test                       # pytest + Vitest
npm run typecheck
```

## Phase 1b — Indicators ✅

**Backend math** — `backend/app/indicators/` (pandas/numpy). The chart API and, in Phase 3, backtests both go through `registry.compute()`, so they always agree.
- `basic.py` (sources, SMA, EMA, RMA, stdev, Bollinger) · `momentum.py` (RSI, MACD) · `volatility.py` (true range, ATR, Supertrend) · `volume.py` (VWAP) · `registry.py` (types, params + defaults, validation, warm-up, `compute`) · `service.py` (`compute_indicators`) · `frame.py`.
- Formulas follow TradingView / Pine v5 (approved before implementing): EMA seeded with the first source value; RMA/RSI/ATR Wilder, seeded with an SMA (NaN until bar n-1; first RSI at bar n); population stdev; Supertrend per Pine's `ta.supertrend` (direction **-1 = up**, +1 = down, first value at bar atr_length-1 with direction +1); flat-series RSI = 100; VWAP resets at each IST midnight, NaN until the day has volume, raises `VolumeRequired` on all-zero volume and is intraday-only.
- **Warm-up** = `max(rule, 500)` bars before the first visible bar; Supertrend `max(10 × atr_length, 1000)`. Rules: SMA/BB n · EMA 8(n+1) · RSI 10n · MACD 8(slow+1)+8(signal+1) · VWAP 24 h of bars. `compute_indicators` loads that much earlier history (as much as exists) and discards it.
- **Same series as the chart:** indicators are computed from `get_candles(...)` with the same symbol, timeframe and session filter, so hidden sessions (muhurat, special_short) never leak into values (tested).
- `POST /api/indicators` `{symbol, timeframe, from?, to?, sessions?, indicators:[{id,type,params?}]}` → `{times, indicators:[{id,type,params(normalised),outputs:{name:[value|null]}}]}`. 404 unknown symbol; 422 for bad params/types/duplicate ids, VWAP on zero-volume or on 1D/1W, unavailable timeframe.
- Measured on real Nifty 5m (all 88k bars, 5 indicators): ~0.25 s, ~15 MB raw JSON (gzipped on the wire). Lazy loading / smaller payloads are Phase 1c.

**Warm-up proof test** (`test_indicators_registry.py`): computing from `visible_start − warm-up` equals full-history values to **0.01 absolute (price units)** for every visible bar, for SMA, EMA 20/50/200, BB, Supertrend (10,3) and (7,2), RSI, MACD, VWAP. Run on a seeded random walk AND on the real NIFTY50 5m and 15m series at 6 start points each (skipped automatically if the gitignored Parquet is absent). Measured: with only 100 bars of warm-up Supertrend is still off by 0.03, RSI by 0.13, MACD by 0.07; at 200+ bars all are exact — so 500/1000 has a wide margin.
- VWAP on real data uses synthetic volume (Nifty has none) — it checks the math/warm-up only.

**Frontend**
- **Indicators** menu: add SMA, EMA, Bollinger, Supertrend (price pane), RSI, MACD (own panes), VWAP; per-row show/hide, settings (length / source / multiplier / fast-slow-signal, colours), duplicate, remove; any number of copies (EMA 20 + EMA 50). Invalid input is flagged and never applied (e.g. MACD fast ≥ slow).
- **VWAP disabled with a tooltip + inline note** on zero-volume symbols (Nifty) and on 1D/1W; a saved VWAP shows "unavailable" in the legend instead of failing, and is never requested.
- Legend under the OHLC line: every visible indicator with its value at the crosshair (latest bar when not hovering); Supertrend coloured/labelled up/down; MACD macd/signal/hist.
- Settings persist in `localStorage` (`chart-analyser.indicators.v1`); corrupted/outdated data is repaired or dropped, never thrown.
- Results are cached per symbol/timeframe/sessions and per indicator type+parameters, so stale values are never drawn; 200 ms debounce on parameter edits; colour changes never refetch (reworked in 1c).
- Supertrend is drawn as two lines (up/down colour) that break at flips (see 1b-polish for how). Bollinger has no fill (later: series-primitive plugin).

**Tests** (all pass): pytest **232** (indicators: math 20, registry 67, service 27, API 13; plus the earlier 105) · Vitest **98** (new: catalog 23, request 10, legend 7, seriesData 5, persistence 4, indicatorStore 14). `npm run typecheck` clean.

**Visually checked in a browser** (via a temporary symbol with volume, since deleted): all 7 indicators together, duplicate EMA with a changed length, volume + RSI + MACD pane stacking, settings surviving a reload, VWAP "unavailable" on 1D, and on Nifty (no volume) the VWAP button disabled with its explanation and no volume pane. Not exercised in a browser: switching between a symbol with and without volume (no symbol selector yet; see Phase 2).

Follow-up applied before 1b: session label `budget_weekend` renamed to **`weekend_full`** (2024-01-20 was not a Budget day) in code, tests, `.env.example`/config defaults and the Sessions menu; sample Parquet re-imported.

## Phase 4 — notes for Pine conversion
- Pine `ta.tr` (no arguments) is **NaN on bar 0**, whereas `ta.atr` (and `ta.tr(true)`) uses `high − low` on bar 0. Our `true_range()` follows the `ta.atr` convention (bar 0 = high − low). Converted strategies that use a bare `ta.tr` in their own formulas must respect the NaN first bar; ATR/Supertrend conversions can use our functions unchanged.
- Pine direction convention is kept for Supertrend (`-1` = uptrend, `+1` = downtrend).
- RSI keeps TradingView's order of checks (`down == 0 → 100`, then `up == 0 → 0`), so a flat series is 100.

## Phase 2 — TODO notes carried over from earlier phases
- Replace the hard-coded `MUHURAT_DATES` list (`backend/app/data/sessions.py`) with Upstox market timings / holidays data (and use it to detect special/short sessions instead of inferring from bar counts). **Still open** (2a labels sessions from the bars themselves, see below).
- ~~Browser-test the volume pane hide/show~~ ✅ done in 2a (RELIANCE ↔ NIFTY, both directions).
- ~~Provide 1m data so the 1m and 3m buttons become available~~ ✅ done in 2a.
- Add the Diwali-2026 Muhurat date once published (until the list is replaced). **Still open.**

## Phase 1b-polish ✅ (visual fixes after the 1b review)
- **All indicator lines are straight** (`lineType: LineType.Simple` set explicitly). Pixel-checked on the real chart: every Bollinger segment sits exactly on the straight chord between its two points (149/149 per band).
- **Supertrend breaks at flips.** `up` has a value only on bars with direction −1, `down` only on +1 (tested on a sequence with 1-bar runs and a warm-up gap). Pixel-checked on the real chart: 15/15 sampled points on the former "diagonals" are now empty.
  - ⚠ Correction to the 1b notes: whitespace points do **not** break a Lightweight Charts line (they are dropped and the neighbours are joined), and a point's colour is used for the segment that **leaves** it (not the one that ends at it — my first attempt had it the wrong way round and the diagonals stayed). So the *last* bar of every run carries the transparent colour `rgba(0,0,0,0)`, which hides the segment to the next run. Same mechanism: VWAP no longer joins yesterday's last value to today's first, and any null gap inside a line breaks it.
- **Legend**: compact box with a semi-transparent background (`bg-black/45`), the hovered bar's date/time in IST (`Tue 29 Sep '26 10:45`; 3-letter month forced because newer ICU prints "Sept"), and a ▾/▸ collapse toggle (remembered in `localStorage`). Collapsed = one line (symbol, timeframe, time).
- **RSI pane**: dashed 30 / 70 lines in a brighter grey (50 line removed). **MACD pane**: solid 0 line. Pixel-checked (dashed rows found at the 70/30 prices, solid row at 0).

**Later (not now):** Bollinger band **fill** (shaded area between upper and lower) using a Lightweight Charts *series primitive* plugin.

## Phase 1c — Lazy loading + performance ✅

**Backend**
- `GET /api/candles` takes `limit` (newest N) and `before` (candles that start before this unix time, exclusive); the response has `has_more`. `limit` together with `from`/`to` → 422. Pages stitch exactly into the full series (tested on real data, incl. the session filter). `data/service.get_candle_page`.
- Indicators for a chunk use the same warm-up rules as always (`POST /api/indicators` with `from`/`to` loads the warm-up history before `from` itself). **Chunk test** (`test_indicators_chunks.py`): values computed chunk-by-chunk equal full-history values within **0.01** for every indicator, on real NIFTY 5m/15m and on a synthetic series with volume (incl. VWAP), incl. the bars right at chunk boundaries and a tiny chunk size.

**Frontend**
- **Open** = newest `INITIAL_BARS = 2000` candles. **Scroll near the left edge** (less than one screen — at least 300 bars — left of the view) → the next older chunk is fetched and prepended. Chunk size grows with what is loaded (min 4k, max 20k) because handing data to the chart costs O(bars already loaded) per prepend.
- **No jump, no flicker**: the candle `setData` and the restore of the visible range (`shiftRange`) run in the same tick, so no intermediate frame is painted. Measured in the browser: the visible *time* range before and after a 4000-bar prepend is identical (bars 250–400 → 4250–4400), and the range-change callback fired exactly twice (no intermediate state).
- **Caches** (all in memory, LRU):
  - candles per `symbol|timeframe|sessions` (10 scopes) — switching timeframe back and forth never refetches (tested; also an older chunk that arrives after you switched away is cached for its own scope);
  - indicator values per scope and per **indicator key = type + parameters** (12 scopes). Colour / visibility / id are not part of the key, so recolouring or hiding+showing never refetches; changing parameters fetches only that indicator; two copies with identical parameters share one computation; going back to earlier parameters is a cache hit. A prepended chunk fetches only the missing older range (`missingRanges`), merged in order (`mergeEntry`, overlap-safe). Identical in-flight requests are de-duplicated; a slow response for a scope you left still lands in that scope's cache.
  - Indicator fetches start immediately for new candles (only settings edits keep the 200 ms debounce).
- Heavy updates are sliced: when an indicator has > 20 000 points its series are handed to the chart one per macrotask, so the page stays responsive.
- Dev-only: `window.__chart` (`{chart, candleSeries}`) and `window.__stores` for console checks; `performance.mark("chart:data-set")`.

**Performance (measured in the IDE browser, 120 Hz display, dev server, 7 indicators)**

| What | Result |
|---|---|
| `GET /api/candles?limit=2000` (15m, via the Vite proxy) | 20–40 ms, 210 KB (35 KB gzipped) |
| `POST /api/indicators`, 5 indicators × 2000 bars | ~30 ms, 356 KB |
| **First chart** (page load → candles on screen, 15m) | **~120 ms** (fetch starts at ~70 ms, data set at ~117 ms); indicators on screen ~175 ms. Target < 1 s ✔ |
| Scroll-to-edge → older 4000 bars on screen (6 000 loaded) | ~90 ms (fetch ~60 ms) |
| Pan, 60 bars/frame across **263 475 bars** (3 × Nifty 5m history, temporary symbol) | 120 fps, p95 9.2 ms, max 9.3 ms |
| Zoom 50 ↔ 50 000 bars, and zoom-to-fit all 263k bars | 120 fps, p99 9.3 ms (one ~1 s stall at the very start of the first run, not reproducible in 5 other runs; I attribute it to the IDE tab waking from throttling) |
| Longest single main-thread block while prepending, with N bars already loaded | N = 24k ≈ 0.1 s · 44k ≈ 0.1 s · 104k ≈ 0.3 s · 264k ≈ 0.9–1.2 s |
| Loading the whole 263k history in 15 prepends | 32 s of wall time in my script (it waits ~1 s after each step; the page stays interactive) |

Scrolling and zooming are cheap at any size (Lightweight Charts only draws what is visible). **The cost that grows is a prepend**: `setData` is O(total bars) per series (~1 µs/point), so each prepend at 100k bars blocks ~0.3 s at worst (Nifty 5m's whole history is 88k bars, so ≤ ~0.25 s in practice), and ~1 s at 264k. Known limit; ideas if it matters (Phase 2, when 1m data makes 1M+ bars possible): keep only a window of bars resident in the chart, or down-sample the far-left part.
- The browser's JS heap reached ~1.4 GB after loading 264k bars × 14 series (mostly garbage waiting for GC; not a leak check).

**Tests**: pytest **276** (+44: candle pages 38, chunk equality 6) · Vitest **149** (+51: cache 15, request 10, chartStore 23, indicatorStore 21, legend 8, seriesData 13, view helpers 8, format 8, …). `npm run typecheck` clean, `vite build` OK.

**Visually checked in a browser**: legend (time, collapse + persistence, size), RSI/MACD guide lines, straight Bollinger, Supertrend/VWAP breaks (pixel scans), prepend without jump (time range identical before/after), full-history load on a temporary 264k-bar symbol (deleted again; `frontend/scratch.html` also deleted).

## Phase 2a — Upstox data token, instruments, symbol selector, 1m history ✅

**Scope**: market data only. No live feed (that is 2b), no OAuth (Phase 7), **no order code at all**: the client has only `GET` methods and a test scans `backend/app` for order endpoints/strings.

**Auth / safety**
- `UPSTOX_ANALYTICS_TOKEN` in `.env` (read-only, ~1 year). Never logged; redacted (token, `Bearer …`, JWT-shaped strings, `Authorization`) from every error/job message. It is only sent to Upstox (instrument download sends no auth header; redirects are not followed).
- Expiry: if the token is a JWT, `exp` is decoded **locally** (yours: valid until 2027-09-30). A token past `exp` is reported `expired` without any network call.
- Header badge **"data token valid / invalid / expired / missing / unverified"** (click = re-check; "N d left" when < 14 days). The check is a cheap data call (`GET /v2/market/status/NSE`), not `/user/profile`, cached 5 min server-side. A bad token never crashes the UI: the selector shows the reason, disables only the *fetch* of new history, and stored symbols still open.
- Rate limiting: one shared sliding-window limiter, **20/s · 300/min · 1200 per 30 min** (our own cap; Upstox allows 50/500/2000 but the budget is shared with My Trading Desk). Own backoff on 429/5xx/network errors (exponential + jitter, honours `Retry-After`); no retry on 401/403/other 4xx.

**Data model**: 1m is the single source of truth; every other timeframe comes from our resampler (measured on the real 440k-bar history, limit 2000: 1m 12 ms · 3m 14 · 5m 16 · 15m 31 · 1h 97 · 1D 284 · 1W 261 ms, so no resample cache was needed).
- Storage: `data/candles/<dir>/1m.parquet` + `meta.json` (instrument info + covered 1m date ranges). `NIFTY50` keeps its old folder name (alias); others use the file-name-safe instrument key (`NSE_INDEX_India_VIX`, `NSE_FO_48704`, `NSE_EQ_INE002A01018`).
- Sync: ≤ 28-day windows (Upstox allows 1 month per 1m request), newest first, only the *missing* ranges, coverage saved after each window (resumable, idempotent). History starts 2022-01-01. **Today** is never marked covered: history endpoint up to yesterday, intraday endpoint for today (it returns nothing on a closed day).
- Importer: drops post-close prints (futures show 15:30–15:39 ticks), keeps OI only for futures/options, labels a partly-fetched day by weekday so a Saturday session is not mistaken for a normal day.
- Defaults: **Nifty 50 full 1m from 2022-01-03** (439,943 bars to 2026-10-01), **India VIX** 1m, **current Nifty future** (NIFTY FUT 27 OCT 26, ~120 days). Other symbols on first selection: stocks ~90 days, futures ~120 days; "Load 3 more months" goes 90 days further back.
- Sessions found in the Nifty 1m: 1169 normal, 3 weekend_full, 4 muhurat, 2 special_short (2024-03-02 and 2024-05-18 are 105-bar special sessions; 2024-01-20, 2025-02-01, 2026-02-01 are full-length).

**Validation (one-time, `scripts.validate_5m`)** → `data/validation/5m_vs_upstox_2026-10-03.md`: our 5m built from 1m vs Upstox's own 5m for Mar 2022, Mar 2024 (special Saturday 2024-03-02), Feb 2025 (Budget Saturday 2025-02-01), Sep 2026, incl. every 09:15 bar. **0 mismatches** in all three comparisons (ours↔Upstox, ours↔saved 5m sample, sample↔Upstox). `data/candles/NIFTY50/5m.parquet` stays as a fixture / cross-check.

**Instruments**
- The public NSE instrument file is snapshotted daily to `data/instruments/YYYY-MM-DD/NSE.json.gz` (atomic write, validated: gzip/JSON, ≥ 1000 rows, contains Nifty 50) so old lot sizes / expired contracts stay on disk.
- **launchd job** `com.tradeboss.instruments-snapshot`, **daily 08:30 IST**, installed. Plus a startup fallback (snapshot taken in a background thread if today's is missing and it is past 06:30 IST).
- Search covers indices, NSE stocks (EQ), Nifty futures / CE / PE only, expired contracts never offered; an empty query lists stored symbols first.

**UI**
- **Symbol selector** (header): debounced search, All / Indices / Stocks / Futures / Options chips, per-row badge (`1m` stored · `no 1m` coarser data only · `fetch`), live sync progress ("fetching 1m history 3/4…"), "Sync latest 1m" and "Load 3 more months" for the current symbol. The legend shows the display name (`NIFTY`, `RELIANCE`, `NIFTY FUT 27 OCT 26`) + timeframe. The 1m / 3m buttons enable when 1m data exists.
- **Bounded window**: at most `MAX_WINDOW_BARS = 60 000` candles in the chart. Scrolling left past the cap drops the newest bars and scrolling right fetches them again (`GET /api/candles?after=…`, `has_more_newer`). The indicator cache is trimmed to the same window and refetches only the dropped ranges. Browser check on the real 1m data: 13 older pages kept it at exactly 60 000 bars, strictly ascending, no errors.
- **Volume pane**, browser-tested: RELIANCE (volume) → NIFTY (none) → RELIANCE again. Pane stack went `price·volume·RSI·MACD` → `price·RSI·MACD` → `price·volume·RSI·MACD`, clean both ways. (RELIANCE was fetched live from the selector: 23,625 1m bars.)

**API added**: `GET /api/upstox/status`, `GET /api/instruments/search`, `POST /api/instruments/snapshot`, `POST /api/history/sync`, `GET /api/history/jobs/{id}`, `GET /api/history/coverage`; `/api/candles` got `after` + `has_more_newer`; `/api/symbols` got `display_name`, `instrument_key`, `kind`.

**CLI** (from `backend/`): `uv run python -m scripts.snapshot_instruments [--force]` · `scripts.backfill --defaults | --symbol NIFTY50 --update | --key "NSE_EQ|INE002A01018" --days 90 | --from 2022-01-01` · `scripts.validate_5m`. launchd: `scripts/launchd/install.sh [--run-now]`, `check.sh`, `uninstall.sh`.

**Tests**: pytest **401** (+125: redaction, rate limiter, client incl. no-order-code scan, token status, instruments/snapshots/search, history windows/coverage/resume, `after` pages, API, real recorded Upstox responses, validation) · Vitest **201** (+52: bounded window, view shift, indicator trim, upstox store, UI helpers). All Upstox calls in tests are mocked with saved responses (two of them recorded from the real API). Typecheck clean, `vite build` OK.

**Facts observed from the real API** (worth knowing for 2b): candles come **newest-first**; index rows carry volume 0; 375 bars per index session, 385 for a future (incl. post-close prints, dropped); the intraday endpoint is empty on a closed day.

**Out of scope / notes**
- **Expired contracts** (expired futures/options history) need Upstox Plus: not supported.
- **For 2b (live feed)**: a normal Upstox account gets **2 websocket feed connections**, and My Trading Desk may already use one. 2b must use at most one and fail gracefully when the limit is hit.
- OAuth (`UPSTOX_API_KEY/SECRET`) moved to Phase 7; `.env.example` marks it as unused.
- Session labels for special days are still inferred from the bars (see the Phase 2 TODO about `MUHURAT_DATES`).

### Phase 2a follow-ups (after approval)
- **Snapshot job skips identical files.** If today's download equals the most recent earlier snapshot (weekends / holidays) it is **not saved again**; the log says `snapshot 2026-10-04 unchanged (identical to 2026-10-02, not saved again)`. A tiny `data/instruments/<day>/UNCHANGED` note (text: the date it equals) stands in for the file, so the startup fallback does not retry all day and `check.sh` can still say "today's job ran". "Identical" = same bytes **or** same bytes after un-gzipping (a gzip header timestamp must not defeat it). `--force` always writes the file. Real snapshots (`list_snapshots`, search, backtests) only ever see files, never notes. Tests: +6 (identical, different gzip header, change-then-compare-with-latest, no re-download on the same day, force, bad download).
- **`validate_5m --key`** now works for any stored symbol (only comparison A runs when there is no saved 5m sample) and adds **per-day volume sums** (ours vs Upstox). **RELIANCE (NSE_EQ|INE002A01018), 2026-09: 1575 5m bars each side (21 days x 75), 0 missing, 0 field mismatches (incl. exact volume), 21/21 days with identical volume sum and bar count, total volume 255,687,056 both sides.** Report: `data/validation/5m_vs_upstox_NSE_EQ_INE002A01018_2026-10-03.md`.

## Phase 2b — Live feed ✅ (built against the documented format; NOT yet seen live: first live session is Monday)

### Groundwork: protobuf + feed probe
- **Protobuf**: `protobuf==7.36.2` and `websockets==17.1` pinned in `backend/pyproject.toml`. `app/upstox/feed/MarketDataFeedV3.proto` (unchanged copy of Upstox's schema, source in the header) and the generated `MarketDataFeedV3_pb2.py(.pyi)` are committed; regenerate with `backend/scripts/gen_feed_proto.sh` (grpcio-tools only in an ephemeral env; gencode 7.35.1 <= runtime 7.36.2).
- **Probe** (`uv run python -m scripts.feed_probe`, Sat 2026-10-03 04:51 IST): authorize -> ONE connection for 10 s -> clean close. **The Analytics Token works for the v3 feed.** Raw frames saved as `backend/tests/fixtures/upstox/feed_probe_2026-10-03.json` (no token, no authorized URL; a test checks). Exactly 2 frames arrived, then nothing (market closed): `market_info` (126 B) and the snapshot (909 B, `type` = initial_feed, which proto3 omits on the wire). No ping seen in 10 s.
- **Findings from the snapshot** (all four keys, mode full = `full_d5`):
  - Indices (Nifty, VIX) arrive as `indexFF` = ltpc + marketOHLC only (no volume, no ltq). Stocks / futures as `marketFF` (+ 5-level depth, atp, vtt, oi, I1 and 1d OHLC). Absent proto3 scalars are 0 (e.g. depth levels with no quote are `{}`).
  - **Post-close ticks are real**: ltt of the Nifty/VIX snapshot is 16:00:00 IST, RELIANCE 15:59:53, the Nifty future 15:39:59 (closing-auction era). So the 09:15-15:30 gate is essential, and `I1` entries with ts >= 15:30 exist (Nifty/VIX I1 = 15:30 flat bar, future I1 = 15:39) and are NOT bars.
  - **RELIANCE I1 (15:29) equals the official 1m bar for 15:29 exactly** (1167.7 x4, volume 1,101,100) although ticks continued until 15:59:53, i.e. I1 did not advance past 15:29 after the close.
  - Official history fills no-trade minutes with a flat bar at the previous close and volume 0 (RELIANCE: 602/602 zero-volume bars are flat at previous close; every day has exactly 375 bars; the future: 2621/2621 flat, one day has 374 bars).

### What was built
- **Backend (`backend/app/live/`)**: `model` (Tick/I1Bar/Bar/Diff), `builder` (candle builder, per instrument and day), `frames` (protobuf -> ticks/I1), `engine` (trading day from `currentTs`/`market_info` only, F6 hold queue, session-end evidence, gap/cold-start logic), `recorder` (+ replayer in `scripts/replay_feed.py`), `connection` (one connection, backoff+jitter, flock, re-authorize on every reconnect), `backfill`, `reconcile` (15:45 and missed days), `overlay` + `persist` (live bars appear in `CandleStore` reads; at session end upserted into `1m.parquet`), `minutelog`, `hub` (browser WebSocket), `service` (wiring, badge state). Routes: `WS /api/live/ws`, `GET /api/live/status`, `POST /api/live/reconcile`.
- **Builder rules** (as approved): bar precedence `filled < tick < i1 < backfill < official`, every overwrite logged as a diff; I1(M) final when an I1 with a later ts arrives or on session-end evidence; flat fills only while connected; late ticks accepted for any non-final bar; a tick whose ltt is >5 s ahead of the same frame's `currentTs` is held, released if a later frame's `currentTs` catches up, else dropped and logged (an illiquid option with a real 25-minute gap is NOT held); recording 09:00-16:05 IST gated by `market_info` (holidays not recorded, special sessions recorded); cold start mid-session backfills 09:15 -> now before live bars; on startup any past day still marked unreconciled is reconciled from the historical API.
- **Frontend**: `live/client.ts` (WebSocket, reconnect 1-10 s, resends the view, 6 s silence watchdog, bar pushes throttled to <=5/s per symbol/timeframe, newest wins), `live/merge.ts`, `live/badge.ts` + `panels/LiveBadge.tsx` (live / stale (Ns since last tick) / reconnecting... / market closed / feed auth failed / feed in use elsewhere / live feed off), `store/liveStore.ts`, `chartStore.applyLive` (ignored while scrolled back), `indicatorStore.applyLiveTail`, ChartView tail path (`series.update`, never `setData` for a live tick) and `IndicatorLayer` tail path (last points only). Higher timeframes and indicators come from the same backend modules as the REST API (`get_candle_page`, `compute_indicators`), so live and REST cannot drift.
- Vite `/api` proxy now has `ws: true`. When the live feed is disabled the WebSocket stays open and reports "live feed off" (no reconnect loop).
- Settings (`.env`): `LIVE_FEED_ENABLED` (default true), `LIVE_OPEN_VOLUME_BASELINE` (`first_tick` default | `pre_open_inclusive`), `LIVE_CONNECT_START/END` (08:55-16:10), `LIVE_RECORD_START/END` (09:00-16:05), `LIVE_RECONCILE_AT/UNTIL` (15:45/16:30).

### Verification
- Backend: **572 tests pass** (builder, engine, recorder/replayer, reconcile, connection, hub, end-to-end service with a fake socket). Frontend: **243 tests pass**, `npm run typecheck` clean, `vite build` OK. No test touches the network.
- Browser smoke test (market closed): badge shows "market closed"; simulated `bar` messages updated the last candle, appended a new one and extended the EMA series by one point (`series.update`, no full redraw).

### Open items for Monday (unknown until we see real frames)
1. Is the feed's I1 the **forming** or the **last completed** bar? (minute log `i1_timing`; the builder works either way.)
2. 09:15 volume baseline: compare `open_bar_volume` candidates against the official bar and set `LIVE_OPEN_VOLUME_BASELINE`.
3. Real latency (`currentTs` vs receive time) and the size of the tick-bar vs I1 vs official differences.
4. Cold start first-tick minute stays `partial` (unknown volume) until I1/official; it is not part of the backfill range.


## Phase 3a — Backtest engine + cost model (engine done; two inputs still open)

### What was built (`backend/app/backtest/`, `backend/app/strategies/`)
- `contracts.py` (Strategy / Signal / Broker; `Signal` got optional `stop`, `target` (OCO bracket at fill) and `oco`), `context.py` (`ctx`: past bars and indicators only; any read past "now" raises `LookAheadError` and is also recorded, so a strategy that swallows it still fails the run), `broker.py` (`BacktestBroker`: orders, intrabar matching, positions, trades), `engine.py` (`run_backtest`, `BacktestConfig`), `costs.py`, `lots.py`, `metrics.py`, `result.py` (canonical JSON, `run_id` = hash of config + strategy + data), `sources.py` (candle store / in-memory).
- Candles: the store's 1m (finest) bars, resampled by the chart's `resample()`; session filter as the chart. Indicators: `app.indicators.registry.compute` (chart code), computed once and revealed one value per bar.
- Rules as approved: fill at the NEXT bar open (or `fill_mode="same_bar_close"`, flagged optimistic); stops fill on touch, at the open when gapped through; limits only when price trades strictly through (better open on a gap); 1m bars inside a higher-timeframe bar decide SL vs target, both in one 1m bar (or no 1m data / missing minutes) = SL first and counted `ambiguous`; square-off default 15:15, no carry unless `allow_overnight`; signal on a session's last bar = `unfilled: no_next_bar`; lot size by trade date from a dated table (`LotSizeAmbiguous` inside a transition window unless the contract's expiry is given, `LotSizeUnknown` before the table starts); costs from a dated, editable JSON table (`UnknownRate` for any rate not on a note); metrics after costs.
- Samples: `EmaCrossover`, `SupertrendFlip`, `OpeningRangeBreakout` (stop-entry OCO pair with bracket stop, one trade per day).
- Real-data smoke run (Nifty 5m, since 2026-07-01, zero costs, 0.08 s each) works; results are index points x lot, not option P&L (that is 3b).

### Tests: `tests/test_bt_*.py` (114 passed, 1 skipped)
- Backend total: **686 passed, 1 skipped**. `uvx mypy app/backtest app/strategies`: clean. Frontend unchanged (243 passed, typecheck clean).
- 1 (no look-ahead: scrambling every bar after T changes nothing before T for 4 strategies x 5m/15m, canary with a leaky indicator is caught, future reads raise, `ctx` indicator values == chart code on the past only), 2 fill timing, 3 intrabar, 4 gaps, 5 sessions, 6 golden (your hand-checked 30 bars), 8 lot sizes, 9 metrics, 10 determinism (byte-identical, PYTHONHASHSEED-independent, run_id), 11 samples.

### Found and fixed while testing
- **Look-ahead in the chart's own indicator code**: `stdev()` (Bollinger) shifted the series by its WHOLE-series mean, so a value changed by ~1 ulp (3.6e-12) when later bars arrived. Now shifted by the first bar (a constant that does not depend on the future). Chart values change by float noise only; all indicator tests still pass.
- `ctx.cancel_orders` renamed `ctx.cancel_working` (the standing "no order code in the backend" test matches the substring `cancel_order`).

### Seeded after approval
- **Lot sizes** (`data/lot_sizes.json`, approved after checking NSE/FAOP/61415 on nseindia.com): Nifty 50 = 50 (from 2021-04-30, NSE/FAOP/47854), 25 (NSE/FAOP/61415: new lot from 2024-04-26, first weekly 2024-05-02, first monthly 2024-05-30), 75 (NSE/FAOP/64625: from 2024-11-20, first weekly 2025-01-02, first monthly 2025-02-27), 65 (NSE/FAOP/70616: circular effective 2025-10-28 EOD -> effective_from 2025-10-29, first weekly 2026-01-06, first monthly 2026-01-27). Keyed on (cycle, expiry date); each row carries its circular number and link. `lot_size(...)` also respects "the new lot only applies once it is in force" for far-month contracts; `lot_size_for_contract()` needs no trade date. Tests: `tests/test_bt_lots_seed.py` (all three changeover windows by contract and by date alone, same-day weekly vs monthly, snapshot cross-check, engine). Not modelled: quarterly/half-yearly contracts already listed at a changeover (they switched on 26 Dec 2024 / 30 Dec 2025 EOD).
- **Cost row 2026-10-01** (`data/cost_rates.json`) is an **UNVERIFIED seed from published rate cards** (Upstox brokerage page and NSE circular NSE/FA/73061, retrieved 2026-10-03): brokerage flat Rs 20, STT 0.15% sell (on premium), exchange 0.03553% (NSE 3,552 + IPFT 1 per crore, as one line), SEBI Rs 10/crore, stamp 0.003% buy, GST 18% on brokerage + exchange (SEBI fee not in the base, per Upstox's page). Every rate is marked `unverified` with its source; a run that uses these rates adds an `UNVERIFIED` warning to its result. New table keys: `verification`, `sources`, `gst_on`, `note`.
- **Test 7d stays skipped**: no contract note yet. When you have one: check (1) one exchange line or two (transaction + IPFT), (2) STT rounding (paisa vs rupee), (3) whether the SEBI fee is in the GST base; then flip the rates to `verified` and un-skip 7d.

## Phase 3a follow-ups A and B ✅ (approved and seeded)
- **A. Cost table** (`backend/app/backtest/data/cost_rates.json`): six dated rows since 2022-01-01 (2022-01-01, 2023-04-01, 2024-04-01, 2024-10-01, 2026-03-01, 2026-04-01), **every rate "unverified"** (NSE/FATAX/56235, 63809, 73524; NSE/FA/56129, 61137, 64232, 73061; Upstox rate card via Wayback). Decisions: NSE IPFT (Rs 50 per crore) and the GST on it are included from 2023-04-01 (conservative, under a paisa per trade either way). Unchanged since 2022: brokerage Rs 20 flat, SEBI Rs 10/crore, stamp 0.003% (buy), GST 18%. The shipped 2026-10-01 seed row was replaced by the 2026-04-01 row (same values). Test 7d stays skipped until a real contract note exists.
- **Phase 7 note (API brokerage)**: Upstox ran a promotional Rs 10 per order for orders placed through the API (its page says valid till 31 Dec 2025, other sources say 31 Mar 2026; it has since ended or changes). Backtests ignore it and use the standard Rs 20 flat. Before live API trading in Phase 7, read the current API-order brokerage from Upstox's charges page / brokerage-details endpoint and add a dated row if it differs.
- **B. Expiry calendar** (`backend/app/backtest/expiry.py`, `data/expiry_rules.json`, `data/nse_holidays.json`, demo `uv run python -m scripts.expiry_calendar_demo`): Nifty weekly = Thursday and monthly = last Thursday for expiries up to 2025-08-31, Tuesday / last Tuesday from 2025-09-01 (SEBI/HO/MRD/TPD-1/P/CIR/2025/76, NSE/FAOP/68589, 68685, 68747); a holiday moves the expiry to the PREVIOUS trading day (Muhurat-only days are not trading days). NSE's Monday plan (FAOP/66938) was deferred (FAOP/67338) and never in force; the Nov-2024 end of other indices' weeklies (FAOP/64506) does not touch Nifty. Reproduces all 105 real Nifty expiries Upstox lists (2024-10-03..2026-09-29), everything in the 2026-10-03 instrument snapshot through 2026-12, and every expiry date in the lot-size circulars. 2022-01..2024-09 is rule-based only. NSE publishes the next year's holidays in December: add 2027 to `nse_holidays.json` then (a date missing there counts as a trading day).
- **Tests**: `tests/test_bt_history.py` (a backtest over 30 days from 2022 to 2026, a day on each side of every lot / cost / expiry-rule change, uses the shipped lot, expiry and cost tables and no NoRatesForDate; right lot, expiry and cost row on each side), `tests/test_bt_cost_history.py`, `tests/test_bt_expiry.py`, `tests/test_bt_lots_seed.py`. Backend suite after 3a: 816 passed, 1 skipped (7d).

## Phase 3b — Option P&L overlay ✅

Estimate option P&L from the engine's index trades. The engine is unchanged; the index JSON is byte-identical with the overlay on or off.

**Expired-candle probe (one request, as asked).** `GET /v2/expired-instruments/historical-candle/NSE_FO|…|03-10-2024/1minute/2024-10-03/2024-10-03` with the Analytics Token returned **375 one-minute bars**. No `UDAPI1149`. Expired history is available on this token, so calibration can use it. Both sources were still designed (you asked for that either way):
- (a) `UpstoxHistorySource` — expired API for past expiries, regular historical API for contracts still listed.
- (b) `RecordedSource` + `capture_listed_day` / `ingest_recorded_bars` — candles we store ourselves (`data/option_history/`), filled by an end-of-day capture (`uv run python -m scripts.capture_option_day`) so the store grows each week. The live feed is not subscribed (Monday's feed check is left alone); a later live recorder writes the same store.

**What was built** (`backend/app/options/`)
- `strikes.py` + `data/strike_steps.json`: step 50 from 2022-01-01, **unverified**. Dates before the table raise `StrikeStepUnknown` (never guessed). ATM ties round up (22,325 → 22,350). `+1` is one strike OTM.
- `contract.py`: long → CE, short → PE; nearest weekly/monthly from the 3a calendar; `roll_on_expiry_day`; lot from `(cycle, expiry)`.
- `bs.py`: Black-Scholes (Hull golden 4.7594 / 0.8086; ATM 183.36); tick snap 0.05 half-up, floor 0.05.
- `time.py`: trading minutes / (375 × 250), or calendar minutes / (365 × 1440) as India VIX. Mon 10:00 → Tue expiry = 705 min (T = 0.00752).
- `vix.py`: India VIX 1m open at the fill minute; last bar within 5 minutes otherwise; older → `vix_stale` still priced; none at all → `unpriced`.
- `model.py` / `result.py`: overlay. The builder default (`OptionModelConfig()`, used by the golden tests) is still **r = q = 0**, slippage **0.5 points/leg**, scale 1.0. Case 22 golden: 25,000 CE 06 Oct 26, fills 126.65 / 150.55, net **1,483.79**. Held-past-expiry settles at intrinsic; exercise STT is not modelled (warned). A run with no config loads **option model v1** (below).
- `events.py`: shipped `event_days.json` is **approved and seeded** (2026-10-03). Full Budget 2024-25 is **2024-07-23**. Added `2024-06-01` exit_poll. A non-trading event date also flags the next session as `reaction_day` (2023-05-13 Sat → 2023-05-15 Mon; 2024-06-01 Sat → 2024-06-03 Mon). Weekend dates that traded (`weekend_full`: 2025-02-01, 2026-02-01) flag on the day itself.
- `history.py` + `calibration.py`: resumable fetch, pair at the same minute (option/index/VIX opens), filters (zero volume, premium < ₹5, first 3 minutes), Huber scale fit, day-block bootstrap, **implied net carry from put-call parity (not applied)**, and **trading-T vs calendar-T per DTE bucket**. Report: `data/validation/option_model_calibration_<date>.md` + `.json`.
- Scripts: `scripts/calibrate_option_model.py` (no network unless `--fetch`), `scripts/capture_option_day.py`.

**Decisions applied**
1. The uncalibrated builder stays r = q = 0 (case 33). The shipped default is option model v1 (below). Every result records `model_version`.
2. Slippage default 0.5 points/leg, configurable.
3. Strike step 50, unverified, from 2022-01-01.
4. Event-day list **approved and seeded** (2024-07-23 full budget; 2024-06-01 exit_poll; reaction_day on the next session when the event is not a trading day).
5. Live calibration `--fetch` ran 2026-10-03: 105 expiries stored (2024-10-03..2026-09-29), 8.70M paired minutes kept. The first fit (one VIX scale **0.888**, r = q = 0) was **not** shipped. Carry was refit and approved as v1.

**Option model v1** (`backend/app/options/data/option_model_v1.json`, as of 2026-10-03, ref `data/validation/option_model_carry_2026-10-03.json`). `overlay_options` loads this when no config is passed.
- Net carry **7.2%** (r = 0.072, q = 0), calendar time, in the forward. Volatility time stays trading minutes.
- VIX scale by DTE bucket: 0 → 0.6489, 1 → 0.8519, 2 → 0.9014, 3–4 → 0.9064, 5+ → 0.9022.
- **Real-premium mode is the default.** A fill uses the stored 1m open of that exact contract at that minute when the bar exists; otherwise the model, and the trade is flagged `modelled`. Results count real vs modelled fills and split P&L the same way (a trade with any modelled fill is in the modelled bucket; an intrinsic expiry settlement is neither).
- Warning `more than 20% of fills are modelled (N of M)` when the modelled share is above 20%. Any trade dated before 2024-10-03 adds `model only: rough check, not proof`.

**2024-12-26 expiry week (one retry, 2026-10-03).** The first fetch had written a done-ledger and no parquet (empty candles marked done). The ledger was cleared and `fetch_expiry` run once more: 24 strikes (23500–24650) × CE/PE, 281 contracts listed, **fetched 0, empty 48, no parquet**. Upstox returned no 1m bars for that week. Not fetched again. Sessions 2024-12-20, 2024-12-23, 2024-12-24 and 2024-12-26 therefore have no nearest-weekly tape (expiry 2024-12-26) and those fills are modelled.

**Tests**: `tests/test_opt_*.py` (72). Reports: `data/validation/option_model_calibration_2026-10-03.md` and `data/validation/option_model_carry_2026-10-03.md`.

**Sample rerun (2026-10-03), real-premium mode, option model v1.** Nifty 5m, 2024-10-03 through the last stored bar (2026-10-01). Index costs zero (so index ₹ = points × the dated lot). Option costs from the dated table (still unverified) plus 0.5 points of slippage per leg. One lot, nearest weekly, ATM. No run crossed the 20% modelled-fill warning (about 1% of fills; the 2024-12-26 week). None is marked model-only.

| strategy | index points | index ₹ | option ₹ after costs | trades | index win | option win | index max DD | option max DD | real fills | modelled fills | real ₹ | modelled ₹ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| EmaCrossover (9/21) | 8,050.65 | 453,016.75 | −60,224.78 | 1,400 | 36.57% | 28.29% | 81,363.75 | 129,413.31 | 2,770 | 30 | −58,401.26 | −1,823.52 |
| SupertrendFlip (10, 3) | 5,146.50 | 251,310.50 | −104,694.09 | 924 | 44.37% | 34.52% | 107,373.75 | 165,490.95 | 1,828 | 20 | −102,564.88 | −2,129.21 |
| OpeningRangeBreakout (15m) | 848.90 | 55,053.00 | 38,622.33 | 492 | 44.31% | 37.60% | 78,723.75 | 60,471.10 | 974 | 10 | 38,482.93 | 139.40 |

## Phase 3c-a — Backtest UI and saved runs ✅

Left drawer stays open while the chart stays mounted. Form: strategy catalog (the three samples), params, symbol, timeframe, start (required) / end (optional), sessions, index vs options (real-premium, NIFTY50 only), strike offset −1/0/+1, option slippage (default 0.5). Square-off 15:15 and next-bar-open are shown and not editable. Index mode runs the engine with zero index costs. Options mode runs that same index pass, then option model v1 with the form's strike offset and slippage.

`POST /api/backtests` returns a job id immediately. One worker. Progress is index bars, then overlay trades. Refresh reattaches from `data/backtests.sqlite` (gitignored). A second start is 409. Saved runs keep the full config, both run ids, model version, the cost rows applied, the data hash, git commit + dirty flag, and the full result. Replay uses the stored config. Same clean commit and same data hash must match the stored canonical JSON (`reproduced` true); a different commit or hash stores the new result with `reproduced` false. A dirty tree is not a reproduction.

**Duplicate with changes** opens the form pre-filled from that run's config (a copy). Results and compare show `code not committed: may not reproduce` when the run's git state was dirty. Results: index vs options side by side, charges and slippage, equity and drawdown (one point per exit; the trough is `max_drawdown`), weekday / time of day / DTE buckets (0, 1, 2, 3–4, 5+) / event flags, sortable trades. Clicking a trade loads a window, switches symbol and timeframe, and places the entry about a third of the way across. Compare is 2–3 saved runs: metrics table and equity on one rupee axis. No combined P&L.

**Tests** (`backend/tests/test_bt_ui.py`, `frontend/src/backtest/present.test.ts`): written first and shown failing (missing modules), then implemented. Save/reload, replay match, replay mismatch, job returns before the engine and a second start is 409, dirty warning, request validation (non-Nifty options, strike 2, unknown params, JobBusy), equity trough, DTE and flags summing to the option net, and the vitest form/sort/jump/compare cases.

**Browser check (2026-10-03).** Opening range breakout, options, NIFTY50 5m, start 2024-10-03, ATM, 1 lot, range 15 minutes. All three opened in Compare. The tree was dirty, so results and compare both showed `code not committed: may not reproduce`. Index net is 55,053.00 on every row (slippage is on the option legs).

| slippage / leg | option net | option max drawdown |
|---:|---:|---:|
| 0.5 | 38,622.33 | 60,471.10 |
| 1.0 | 6,888.04 | 81,702.75 |
| 1.5 | −24,754.99 | 1,04,512.76 |

The 0.5 row matches the 3b sample. Walk-forward is phase 3c-b, below.

## Phase 3c-a follow-up — Spread recorder (built, left off)

Addition to the phase 2b feed. `SPREAD_RECORDER_ENABLED` defaults to **off**, so the live subscription list is unchanged until it is turned on. With it on, the same connection also takes the nearest weekly Nifty expiry, ATM ± 2 strikes, call and put (10 contracts, full mode). A new ATM has to hold for 2 seconds. The previous set stays until each new contract has printed a book, or 30 seconds pass. Option keys stay at or under 20; a further move drops the oldest set. Chart keys keep their own cap of 12 and are listed first.

Every depth update is written to `data/spreads/YYYY-MM-DD.parquet` (a same-day restart continues in `YYYY-MM-DD.part-N.parquet`), inside the existing 09:00–16:05 gate and only while the feed says a segment is open. The row is the feed clock, best bid and ask and their quantities, the spread, the Nifty last price, and the lot size from the lot table. From the five levels it also stores the average fill for buying and for selling 1 lot and 2 lots, as points away from the mid, and whether level 1 alone covers 1 lot. A quantity the five levels cannot fill is null.

The daily report (`uv run python -m scripts.spread_report --date YYYY-MM-DD`, and once when the feed stops) takes percentiles from **one snapshot per contract per second** — the last update in that second — so a busy second is not over-weighted. Median, 90th percentile, count, and the uncovered count, by 15-minute bin, DTE (0, 1, 2, 3–4, 5+), and moneyness (ATM, ITM1, ITM2, OTM1, OTM2, outside), plus the fast-minute slice (Nifty 1m range in the top 10% that day, ties included) and the 5 minutes after the first 1m bar that breaks the 09:15–09:30 range. A cell with fewer than 30 snapshots is marked thin.

**Not built yet.** After 10 sessions the backtest form can offer the measured 1-lot fill cost (buy: ask side, sell: bid side) for that fill's DTE, 15-minute bin, and fast-minute flag, in place of the fixed slippage number. A missing cell keeps the fixed number and warns.

**Tests:** `backend/tests/test_spread_recorder.py`. Written first (collection failed: `app.live.spreads` did not exist), then implemented. Fixture depth frames only. Live service tests still pass with the flag off.

## Phase 3c-b — Walk-forward ✅

Options only, NIFTY50, real premiums, option model v1. Default slippage for a walk-forward is **1.0** points per leg (a normal Run stays at 0.5). Train 6 months, test 2, step 2. Minimum 30 closed train trades. Lots stay 1, mode stays long/short. Slippage is not searched. Grids: EMA fast 5/9/12 × slow 15/21/34 (9), Supertrend ATR 7/10/14 × multiplier 2/3/4 (9), ORB range 5/15/30 (3). A grid over 50 combinations is rejected.

The holdout is frozen in `backend/app/backtest/data/holdout.json` as **2026-07-01 through 2026-10-01**. It does not move when new sessions arrive. Research ends **2026-06-30**. Sessions after 2026-10-01 are the forward period; walk-forward does not read them unless `include_forward` is set, and it never reads the holdout itself. The peek count belongs to that fixed range. Every result records it. The sentence is `final holdout has been run N times`. The final-holdout action confirms, then increments the count before the engine starts. **It was not run.** The count is 0.

A window whose best eligible train net is not positive records `no choice: best train net not positive` and stays flat in its test window (counted, shown). Degradation (test net-per-trade / train net-per-trade) is only for windows with a positive train net-per-trade. Stitched out-of-sample equity appends the chosen test trades and recomputes drawdown on that curve. Compare uses that curve.

**Tests** (`backend/tests/test_walk_forward.py`, `frontend/src/backtest/present.test.ts`): written first. Collection failed with `No module named 'app.backtest.holdout'`; the vitest helpers were missing. Then implemented. Hand example (equity 30, 20, 25, net 25, max drawdown 10, both ratios 1/3, one param change, `walk-forward tried 4 combinations`), minimum trades and ties, a non-positive train staying flat, test-window data not changing the choice, the frozen holdout and a holdout-only file left out of the research hash, peek count surviving a failed second accept, determinism, and the request rejections including a second start (409).

**Walk-forward runs (2026-10-03), defaults, slippage 1.0, research 2024-10-03 through 2026-06-30, 7 windows.** The tree was dirty (`code not committed: may not reproduce`). Holdout peek count stayed 0.

| strategy | OOS option net | OOS max drawdown | OOS trades | combinations | param changes | windows with a choice |
|---|---:|---:|---:|---:|---:|---|
| EMA | −3,593.42 | 22,868.03 | 56 | 63 | 0 | 1 of 7 (12/34 on 2026-04-03..2026-06-02; test −3,593.42, degradation −2.74). The other six stayed flat. |
| Supertrend | −9,112.85 | 46,802.41 | 147 | 63 | 1 | 3 of 7. ATR 14 × 4 then ATR 7 × 4. Test nets −33,149.41, +22,088.96, +1,947.60. Degradations −1.87, 8.62, 0.30. |
| ORB | 9,739.55 | 42,020.24 | 205 | 21 | 2 | 5 of 7. Range 5, then 15, then 5. Test nets +23,232.90, −2,736.14, +10,447.67, −17,090.13, −4,114.75. The last two windows stayed flat. |

## Phase 4a — Pine ports (code only)

Three SpringPad scripts, from the Pine sources: Pivot Extension, Log XZ, Price Channel. Each has two execution modes. `realistic` is the default: the engine's 15:15 square-off, 1-minute ordering inside a bar, no overnight position. `tv_parity` matches what TradingView does with these scripts. The square-off is `strategy.close(..., when=time(timeframe.period, "1515-1520"))`. On a 5-minute or 15-minute chart that `time()` is na: the session is five minutes long, and no bar of the chart resolution fits inside it, so the close is never sent. Positions carry overnight, and the last trade stays open when the loaded range ends. A stop and a target inside one bar follow Pine's open→high→low→close or open→low→high→close path.

Pivot Extension `faithful` uses a pivot only on the bar that confirms it. In `realistic` a missing stop is a market order. In `tv_parity` that entry is skipped and the previous order with the same id stays working. The long side updates on a confirmed pivot low while flat, the short side on a confirmed pivot high while in a position. `carried_pivots` is Vaibhav's research variant: stops rest on the most recent confirmed pivot high and pivot low, carried forward. It is named as that variant. Walk-forward's default grid is both variants × 5m and 15m, so the research variant is inside the tried total. Log XZ defaults to RMA(close, 14); a buy is the previous XZ ≤ 0 and the current XZ > 0, and XZ is log(average) one bar ago minus log(average) four bars ago. Price Channel is stop-and-reverse, with the channel including the bar that just closed. 5m and 15m are a walk-forward axis for all three (Log XZ lengths 10 and 14; channel lengths 20 and 40).

**Tests** (`backend/tests/test_pine_ports.py`, and the phase 3a no-look-ahead test on every port in both modes): written first. Collection failed (`app.strategies.log_xz` did not exist). Then implemented. Backend suite: 956 passed, 1 skipped.

**TradingView parity (2026-10-03).** NIFTY 5m, script defaults, quantity 1, commission 0, slippage 0, empty state from the first stored bar on 2026-06-22 (09:15) through 2026-10-01. No square-off. That window overlaps the final holdout (2026-07-01 through 2026-10-01), so for these three strategies the holdout is not clean. Their clean test is data after 2026-10-01. Closed-trade results against the TradingView lists (175 / 351 / 267 closed; the last trade left open):

| script | our closed | TV closed | our wins | TV wins | our net | TV net | our PF | TV PF | our closed-trade max DD | TV max DD (includes open trades) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Pivot Extension | 518 | 351 | 183 | 136 | +43.60 | +692.60 | 1.005 | 1.123 | 1,303.85 | 1,097.40 |
| Log XZ | 279 | 267 | 94 | 97 | −1,055.80 | +988.00 | 0.838 | 1.212 | 2,629.35 | 1,046.05 |
| Price Channel | 173 | 175 | 68 | 77 | +615.70 | +1,968.70 | 1.118 | 1.416 | 969.50 | 1,033.50 |

Price Channel's last four closed trades and the open long match TradingView's #172–#176 in time, direction, and price (the list is two trades shorter, so our #170 is their #172). Trade #149 on a start that forces 175 closed trades (2026-06-19 12:35) is the short that exits 2026-09-11 13:50, and the cumulative after it is 134.05, not TradingView's 1,348.65. No session open from 2026-06-01 through 2026-07-16 reproduces the three trade counts together, or that cumulative. Log XZ's last trades are the same reversals, with fills 0.05–0.20 points off our 5m opens and one entry a bar earlier. Pivot's Oct 1 14:25 long matches their #351 except the exit, which is our 14:50 open (22358.25) against their 22358.15; the trades before it do not match. Market fills are the bar's open in our engine. Several of TradingView's market prices are 0.05–0.20 off those opens (Upstox versus TradingView's tape). Stop fills are the stop level, not the open; the Price Channel stops in the matched tail equal our levels exactly.

Overnight versus same-day, this run, closed trades only: Pivot +940.95 overnight (71) and −897.35 same-day (447). Log XZ +1,215.55 (68) and −2,271.35 (211). Price Channel +2,045.25 (67) and −1,429.55 (106).

**Realistic options (2026-10-03).** NIFTY50, 5m, script defaults, 15:15 square-off, real premiums, ATM, slippage 1.0 per leg. Research window 2024-10-03 through 2026-06-30. Index costs are zero, so index ₹ is points times the dated lot. Not saved: other files in the tree were still uncommitted. Holdout peek count stayed 0.

| strategy | index ₹ | option ₹ | trades | index win | option win | index max DD | option max DD | real fills | modelled fills |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Pivot Extension (faithful, 4/2) | 465,988.75 | 145,391.26 | 3,385 | 36.69% | 32.05% | 80,089.75 | 72,026.09 | 6,728 | 42 |
| Log XZ (RMA 14) | 285,241.25 | −305,678.93 | 1,735 | 38.27% | 26.46% | 74,250.00 | 315,367.69 | 3,422 | 48 |
| Price Channel (20) | 630,280.25 | 481,810.37 | 1,098 | 45.36% | 40.89% | 49,327.50 | 34,407.39 | 2,174 | 22 |

**Walk-forward, same settings, no holdout.** Train 6 months, test 2, step 2, minimum 30 train trades. Seven windows. Grids: pivot faithful/carried_pivots × 5m/15m; Log XZ length 10/14 × 5m/15m; channel length 20/40 × 5m/15m. 28 combinations each. Peek count stayed 0.

| strategy | OOS option ₹ | OOS max DD | OOS trades | combinations | param changes | windows with a choice |
|---|---:|---:|---:|---:|---:|---|
| Pivot Extension | 279,792.51 | 58,534.27 | 861 | 28 | 1 | 7 of 7. carried_pivots, 5m for the first four tests, then 15m. Test nets +132,159.21, +62,565.05, +30,062.38, +33,096.11, −2,111.31, +51,829.71, −27,808.64. |
| Log XZ | 5,332.14 | 26,478.05 | 96 | 28 | 0 | 2 of 7. The first five stayed flat (best train net not positive). The last two chose RMA 14 on 15m. Test nets +23,733.48 and −18,401.34. |
| Price Channel | 352,152.29 | 34,407.39 | 731 | 28 | 0 | 7 of 7. Length 20 on 5m every window. Test nets +102,174.87, +62,505.99, +11,101.70, +26,453.05, +41,696.76, +50,464.50, +57,755.42. |

The final holdout was not run.

**Option fill on a stop (2026-10-03).** A real-premium fill is looked up at the index fill's timestamp, which is the start of the 1-minute bar that touched the stop. `premium_at` is that minute's option open, so a stop that triggers after the open was buying the option at the price from before the breakout. Market orders, and stops gapped through the open, stay on that open. `option_fill` is now a run setting: `minute_open` (that open), `adverse` (buyer pays the higher of the minute's open and close, seller receives the lower), `worst` (buyer pays the minute's high, seller receives the low). Saved on `b5ce7ca`, tree clean. Peek count stayed 0.

Same realistic window as above, slippage 1.0. Index ₹ does not change with the fill setting.

| strategy | fill | option ₹ | option win | option max DD |
|---|---|---:|---:|---:|
| Pivot Extension | minute_open | 145,391.26 | 32.05% | 72,026.09 |
| Pivot Extension | adverse | −748,620.37 | 26.29% | 762,594.01 |
| Pivot Extension | worst | −1,154,991.11 | 24.76% | 1,168,705.17 |
| Log XZ | minute_open | −305,678.93 | 26.46% | 315,367.69 |
| Log XZ | adverse | −305,678.93 | 26.46% | 315,367.69 |
| Log XZ | worst | −305,678.93 | 26.46% | 315,367.69 |
| Price Channel | minute_open | 481,810.37 | 40.89% | 34,407.39 |
| Price Channel | adverse | −156,265.20 | 32.79% | 199,031.54 |
| Price Channel | worst | −436,996.88 | 30.78% | 449,465.08 |

Log XZ is unchanged across the three settings: its entries are market orders at the bar open. Pivot and Price Channel, which enter on stops, go from strongly positive to negative once the option is priced through the trigger minute.

**Walk-forward with `adverse`, same window, no holdout.** Peek count stayed 0.

| strategy | OOS option ₹ | OOS max DD | OOS trades | param changes | windows with a choice |
|---|---:|---:|---:|---:|---|
| Pivot Extension | −108,668.21 | 122,802.65 | 383 | 1 | 4 of 7. Windows 1, 3 and 6 stayed flat. Faithful 15m, then faithful 15m, then carried_pivots 15m, then carried_pivots 15m. Test nets −10,394.43, −23,544.93, −23,521.68, −51,207.17. |
| Log XZ | 5,332.14 | 26,478.05 | 96 | 0 | 2 of 7. Same as the minute-open walk-forward: the first five stayed flat, then RMA 14 on 15m. Test nets +23,733.48 and −18,401.34. |
| Price Channel | −14,183.70 | 71,950.19 | 93 | 0 | 3 of 7. Length 40 on 15m. Test nets +24,093.91, −13,824.89, −24,452.72. The last four windows stayed flat. |

**Pivot `stop=na` (2026-10-03).** Same empty-state window as the parity table, quantity 1, no costs, no slippage. TradingView's #350 short stays open from 2026-09-30 12:15 until the 2026-10-01 14:25 stop, and the list has 351 closed trades. Turning a missing stop into a market order still produces 518 closed trades and several reversals through that afternoon. Skipping the missing stop and leaving the previous order working produces 13 closed trades, net −255.10, and the last closed trade exits 2026-09-01 10:50. Cancelling the missing side instead produces 11 closed trades, net −183.10, and the same last exit. Neither version is the Sep 30 short, and neither is 351. `tv_parity` keeps the previous order. Pivot parity is unresolved and parked: neither `stop=na` hypothesis reproduces TradingView's 351 trades, and the strategy loses under realistic fills.

**Delta-adjusted stop fills (2026-10-03).** Default for a stop inside the minute: option open plus the option-model-v1 delta (call positive, put negative) times the index move from that minute's open, clamped to the option minute's high and low. Market fills stay at the open. The old minute-open fill is now `optimistic` and the result says so. Saved on `835e8cf`, tree clean. Peek count stayed 0. Same realistic window and slippage 1.0. Index ₹ is unchanged.

| strategy | option ₹ | option win | option max DD |
|---|---:|---:|---:|
| Pivot Extension | −509,817.79 | 27.89% | 534,738.16 |
| Log XZ | −305,678.93 | 26.46% | 315,367.69 |
| Price Channel | 8,826.14 | 35.43% | 110,523.49 |

**Walk-forward, `delta_adjusted`, same window, no holdout.** Peek count stayed 0.

| strategy | OOS option ₹ | OOS max DD | OOS trades | param changes | windows with a choice |
|---|---:|---:|---:|---:|---|
| Pivot Extension | −41,495.51 | 82,817.65 | 567 | 1 | 6 of 7. The first window stayed flat. Faithful 15m for the next three tests, then carried_pivots 15m. Test nets +4,096.62, +1,799.24, −17,680.91, −16,110.32, +32,474.38, −46,074.52. |
| Log XZ | 5,332.14 | 26,478.05 | 96 | 0 | 2 of 7. Unchanged from the open fill: RMA 14 on 15m for the last two windows (+23,733.48, −18,401.34). |
| Price Channel | −30,607.41 | 93,341.02 | 308 | 2 | 5 of 7. Length 40 on 15m, then length 20 on 5m, then length 40 on 15m. Windows 5 and 6 stayed flat. Test nets +27,915.16, −3,474.75, −31,586.05, −18,224.23, −5,237.54. |

**Pine panel (Phase 4b, 2026-10-03).** The Pine editor is in the app. The scanner owns the session trap: a window is a hit on a timeframe when no chart bar, aligned from 09:15 and ending at or before 15:30, lies fully inside it. A bar that ends exactly on the window end does not fit. `1515-1520` is a hit on 5m and 15m. The pasted script is sent to the model as data to analyse. A comment telling the model that the session close works still leaves the scanner hit, and the card shows that the scanner and the model disagree. `stop=na` labels `tv_parity` "unverified: stop=na behaviour on TradingView not reproduced". A strategy saved under `app/strategies/user/` runs in a worker subprocess for the save check and for a normal backtest. That worker does not receive `OPENAI_API_KEY` or the Upstox token. The holdout was not run. Peek count stayed 0.

**Pine conversion fixes (2026-10-04).** `time()` session strings that arrive through an `input()` default or a simple assignment are resolved. A session that cannot be resolved is `unresolved: check by hand`, never clear. Price Channel's `1515-1520` input is a hit on 5m and 15m, and the overnight trap is a hit. The model saying the close does not fire while the scanner says clear or unresolved is a disagreement. A draft that is not a Strategy subclass is not a ready draft; the converter repairs at most twice. Conversion uses its own model (`gpt-5.4` on this account, at least 8000 output tokens). The report still uses `gpt-4o-mini`. Generated tests that do not run are dropped.

**Price Channel entry window (2026-10-04).** `entry_window` was using the bar's start minute, so a 15-minute bar starting at 14:45 counted as inside 09:15-14:50. It now uses the scanner rule: the bar fits only when it starts at or after 09:15 and ends strictly before 14:50. The 14:45 bar of a 15-minute chart ends at 15:00 and does not arm. A 5-minute bar that ends exactly at 14:50 does not arm either. A stop armed on an earlier bar still fills after the window. Realistic hand-port rerun, NIFTY, slippage 1.0, delta-adjusted options, sessions normal and weekend_full. Holdout was not run.

| window | timeframe | length | trades | index ₹ | option ₹ |
|---|---|---:|---:|---:|---:|
| 2026-04-01 .. 2026-06-30 | 5m | 20 | 163 | 93,034.50 | −2,224.21 |
| 2024-10-03 .. 2026-06-30 | 5m | 20 | 1,089 | 633,199.75 | 16,621.49 |
| 2026-04-01 .. 2026-06-30 | 15m | 20 | 71 | −3,562.00 | −45,204.07 |
| 2026-04-01 .. 2026-06-30 | 15m | 40 | 45 | −29,939.00 | −33,002.64 |
| 2024-10-03 .. 2026-06-30 | 15m | 20 | 506 | 187,840.75 | −73,280.70 |
| 2024-10-03 .. 2026-06-30 | 15m | 40 | 342 | 240,411.50 | 11,318.45 |

The previous full-window 5-minute result (1,098 trades, index 630,280.25, option 8,826.14) used the start-minute rule.

**Pine report network error (2026-10-04).** The OpenAI call connects directly. A proxy that refuses the CONNECT tunnel no longer becomes an empty HTTP 500; the report endpoint returns the failure text and the panel shows that text.

**Pine gates (2026-10-04).** The AST allow-list accepts exactly `from __future__ import annotations` and rejects every other `__future__` import. A failed check sent back to the model names the source line and the change, for example `remove line 1: from __future__ import print_function`. Convert refuses unless that exact report id and hash was accepted through `/api/pine/accept`. Save refuses unless that exact diff hash was approved through `/api/pine/approve`. Editing the report or the diff after that record is refused. A `accepted: true` field in the convert body is ignored. The holdout was not run. Peek count stayed 0.
