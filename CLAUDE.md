# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Run from the repo root unless noted. Node deps are installed by `npm run setup`; Python deps by `uv sync` in `backend/`.

- Setup: `make setup` (or `npm run setup`)
- Start backend (port 8000) and frontend (port 5173) together: `make dev`
- All tests: `npm test` (runs backend pytest, then frontend Vitest)
- Backend, one file: `cd backend && uv run pytest -q tests/test_bt_lookahead.py`
- Backend, one test: `cd backend && uv run pytest -q tests/test_bt_lookahead.py::test_name`
- Frontend, one file: `npm --prefix frontend run test -- src/chart/format.test.ts`
- Typecheck (strict TS): `npm run typecheck`
- Frontend build: `npm --prefix frontend run build`
- No linter is configured in the repo.

## Architecture

The app is a local trading workstation: a FastAPI backend (`backend/app/`) serves market data, backtests, and analysis to a React + Lightweight Charts v5 frontend (`frontend/src/`). Vite proxies `/api` and `/ws` to the backend. The read-only Upstox analytics token is the only broker credential; nothing places orders (`LIVE_TRADING` stays false).

**Candle data.** Candles live as Parquet at `data/candles/<SYMBOL>/<N>m.parquet` and are read through DuckDB (`app/data/store.py`). The finest file is the base; `app/data/resampler.py` builds the other timeframes, anchored to 09:15 IST. Each bar carries a `session_type` (normal, weekend_full, special_short, muhurat) set in `app/data/sessions.py`. Charts and backtests filter by session type. Live rows reach the store through an overlay provider (`set_overlay`) and are never fabricated for missing timeframes.

**Live feed.** `app/live/` is one pipeline: `connection` (Upstox websocket, one connection max) → `recorder` → `engine` (builds bars from ticks) → `overlay` and `persist` → `hub` (pushes to the browser over `/ws`). `live/service.py` documents the threading rule: engine state is touched only on the asyncio loop under `_elock`, and blocking I/O runs in worker threads that hand results back. Bars use exchange time only; the local clock is only for scheduling.

**Backtest.** `app/backtest/engine.py` runs bar by bar. Strategies implement the `Strategy` and `Signal` contracts in `backtest/contracts.py` and see only closed bars. Orders are matched against 1m bars, and a signal fills from the next bar unless it is explicitly marked optimistic. Indicators are computed once over the series and revealed one value per bar. `tests/test_bt_lookahead.py` enforces this. A fixed holdout period is in `backtest/holdout.py`; research runs must not use it.

**Strategies.** Python strategies live in `app/strategies/` (`user/` holds your own). Pine Script strategies go through `app/pine/`: an AI-assisted conversion to Python, an AST gate that allows only listed imports, and execution in an isolated worker (`pine/isolated.py`, `pine/worker.py`). The Pine path and the hand-ported path share one context, and `tests/test_worker_parity.py` checks they agree.

**Options.** `app/options/` estimates option P&L as an overlay on index-signal backtests (Black-Scholes in `bs.py`, with lot sizes, expiry rules, and the calibrated model JSON under `app/options/data/`). It never produces live orders.

**AI analysis.** `app/ai/` sends a compact chart context to OpenAI and expects one JSON object matching `ai/schema.py`. Prices in the reply must sit inside the allowed band around the last price. The repair loop retries at most twice. Tests use a fake client; no test opens a socket.

**Frontend.** `App.tsx` composes the page. `store/` holds Zustand stores (`chartStore`, `indicatorStore`, `liveStore`, `backtestStore`, `upstoxStore`), `api/` holds the REST clients, `chart/` wraps the chart, `draw/` holds drawing tools (anchors are stored as time and price, so they survive timeframe changes), `replay/` plays stored candles up to a cursor, and `pine/` and `ai/` hold the panels' presentation logic.

## Project rules

- `PROJECT_PLAN.md` is the spec and the phase order. `PROGRESS.md` is the status log: add to it after each phase.
- Critical modules (backtest engine, cost model, candle builder, resampler, risk manager) get their tests written before the code. Upstox calls are mocked in tests; tests never hit the live API.
- Commit and push every change, then confirm a clean working tree before reporting a step as done (from `.cursor/rules/commit-before-done.mdc`). Never commit `.env` or anything under `data/`. Ask before leaving a file uncommitted.
- Never print or commit the contents of `.env`. Tokens are read through `SecretStr` and must not reach logs or responses.
- If unsure about an Upstox endpoint, read the official docs rather than guessing.
