# Personal Chart Analyser — Master Plan for Cursor

> Put this file in the root of the new project folder. Start every Cursor session with:
> "Read PROJECT_PLAN.md. We are on Phase N. Do only Phase N."

---

## 1. Vision

A personal, locally run trading workstation that:

1. Charts as smoothly as TradingView (candles, zoom, pan, crosshair, timeframes, indicators, drawings).
2. Backtests strategies — Nifty 50 first, any NSE symbol later.
3. Streams live data from Upstox into the chart when running locally.
4. Lets me add strategies in Python **or** paste Pine Script and see signals on the chart.
5. Gives AI trend analysis of the current chart (like gettrade.ai's analyser).
6. Later: paper trading → live trading through Upstox, using the **same strategy code** that was backtested.

The last point drives the whole architecture: one strategy interface for backtest, paper and live.

---

## 2. Tech stack (decided)

| Layer | Choice | Why |
|---|---|---|
| Frontend | React + TypeScript + Vite | Fast local dev, simple, Cursor handles it well |
| Charts | TradingView **Lightweight Charts v5** (open source, Apache-2.0) | Same company as TradingView, canvas-based, very smooth with large datasets |
| Drawing tools | Lightweight Charts plugin API (custom primitives) | Trendline, horizontal line, ray, rectangle, fib — built in phases |
| UI state | Zustand | Small, simple global state |
| Styling | Tailwind CSS, dark theme by default | |
| Backend | Python 3.12 + **FastAPI** + uvicorn | Python has the best backtesting/data ecosystem |
| Broker | Upstox API (official `upstox-python-sdk`) | Auth, historical candles, market-data WebSocket, orders |
| Live stream to UI | FastAPI WebSocket → browser | Backend builds candles from ticks, pushes to chart |
| Candle storage | **DuckDB + Parquet** files | Fast local analytics, no server to run |
| App data | SQLite (strategies, backtest runs, trades, settings) | |
| Indicators | pandas + numpy (+ `pandas-ta` or TA-Lib) | |
| Backtest engine | Own event-driven engine (bar-by-bar) | Same code path for backtest/paper/live; no look-ahead |
| Pine Script | **PineTS** (open-source Pine→JS runtime) for indicators; AI-assisted Pine→Python conversion for strategies | See section 6 |
| AI analysis | OpenAI API (key in `.env`) | Structured JSON analysis of chart data |
| Secrets | `.env` file, never committed | |

---

## 3. Folder structure

```
chart-analyser/
├── PROJECT_PLAN.md
├── .env.example
├── frontend/
│   └── src/
│       ├── chart/          # chart wrapper, indicators panes, drawing tools
│       ├── panels/         # watchlist, strategy panel, backtest results, AI panel
│       ├── store/          # zustand stores
│       └── api/            # REST + websocket clients
└── backend/
    └── app/
        ├── main.py
        ├── broker/         # upstox auth, historical, market feed, orders
        ├── data/           # duckdb store, candle builder, instrument master
        ├── indicators/
        ├── strategies/     # base.py + one file per strategy
        ├── backtest/       # engine, cost model, metrics
        ├── pine/           # pine handling
        ├── ai/             # analysis prompt + schema
        └── execution/      # paper broker, live broker, risk manager (later)
```

---

## 4. Core contracts (do not break these)

### Candle
```python
Candle = { "time": int (unix seconds, IST-aware), "open": float, "high": float,
           "low": float, "close": float, "volume": float, "oi": float | None }
```

### Strategy interface — used by backtest, paper AND live
```python
class Strategy:
    name: str
    params: dict
    def on_start(self, ctx): ...
    def on_bar(self, bar, ctx) -> list[Signal]: ...   # only sees bars up to now
    def on_stop(self, ctx): ...

Signal = { "side": "BUY"|"SELL"|"EXIT", "qty": int, "type": "MARKET"|"LIMIT"|"SL",
           "price": float|None, "tag": str }
```
`ctx` gives: past bars, indicators, current position, cash. **No access to future bars.**

### Broker interface — swapped, never rewritten
```python
class Broker:   # implementations: BacktestBroker, PaperBroker, UpstoxLiveBroker
    def place(self, signal) -> OrderId
    def cancel(self, order_id)
    def positions(self)
```

---

## 5. Phases

Each phase ends with something I can run and check. Do not start the next phase until the current one works.

### Phase 0 — Skeleton
- Create folders, Vite React TS app, FastAPI app, `.env.example`.
- One command to start both (e.g. a `dev` script or `make dev`).
- Health check endpoint, frontend shows "backend connected".

### Phase 1 — Chart core (the "smooth like TradingView" phase)
- Lightweight Charts candlestick chart filling the screen, dark theme.
- Load sample Nifty 50 data from a local file.
- Timeframe buttons: 1m, 3m, 5m, 15m, 30m, 1h, 1D, 1W (resample on backend).
- Mouse-wheel zoom, drag pan, crosshair with OHLC legend at top-left.
- Volume pane. Indicators: EMA, SMA, VWAP, RSI pane, MACD pane, Bollinger Bands, Supertrend. Add/remove from a menu, edit params.
- Lazy-load older history when scrolling left.
- Performance target: 100k+ bars without lag.

### Phase 2 — Upstox data
- Upstox OAuth login flow from the app (token is daily — handle expiry and re-login cleanly).
- Instrument master download and symbol search (Nifty 50 index first: `NSE_INDEX|Nifty 50`).
- Historical candles → stored in DuckDB/Parquet; only fetch what's missing.
- Live: Upstox market-data WebSocket → backend tick handler → candle builder → push to frontend over WebSocket. Last candle updates live on the chart.
- Reconnect logic, market-hours awareness (IST, 9:15–15:30), holidays.
- Symbol selector so any instrument can be opened.
- **Read the official Upstox API docs for current endpoint versions, limits and feed format before coding.**

### Phase 3 — Backtesting engine
- Bar-by-bar engine using the Strategy + BacktestBroker contracts.
- Fills on next bar open by default (configurable); no look-ahead.
- Cost model: brokerage, STT, exchange charges, GST, stamp duty, slippage. Separate presets for index/equity and **options** (I trade options only, mostly ATM ± 1 strike).
- Results: trades table, equity curve, drawdown chart, stats (net P&L after costs, win rate, profit factor, max DD, expectancy, avg trade, number of trades, Sharpe).
- Trade markers (entry/exit arrows) drawn on the main chart; clicking a trade jumps to it.
- Date-range picker, in-sample / out-of-sample split, walk-forward option.
- Save every run to SQLite with params so runs can be compared.
- Sample strategies: EMA crossover, Supertrend, ORB.

### Phase 4 — Pine Script
- Paste Pine Script in an editor panel (Monaco editor).
- **Indicators**: run with PineTS in the frontend/Node, plot outputs on the chart. Show a clear error for unsupported functions.
- **Strategies**: button "Convert to Python strategy" → AI converts Pine to a `Strategy` class → I review the code diff → save into `strategies/` → backtest it.
- Verify conversions: compare signals vs TradingView on the same dates for a few samples.

### Phase 5 — AI analyser
- "Analyse" button on the chart.
- Backend builds a compact context: last N candles on 2–3 timeframes, key indicators, swing highs/lows, support/resistance, VWAP, day's range, option-relevant levels.
- Optional chart screenshot sent as an image.
- OpenAI call returns **strict JSON**: trend (per timeframe), bias, key levels, patterns, scenario bull/bear with trigger levels, confidence, reasoning.
- Render as a side panel; draw returned levels on the chart.
- Save each analysis with timestamp so accuracy can be reviewed later.
- AI output is context, never an automatic order.

### Phase 6 — Paper trading
- PaperBroker using live Upstox data and the same strategy code.
- Live P&L panel, positions, order log.
- Daily report of paper trades.

### Phase 7 — Live trading (only after paper results are good)
- UpstoxLiveBroker behind the Broker interface.
- Risk manager in front of every order: max loss per day, max trades per day, max qty, allowed hours, one-click **kill switch** that cancels all orders and exits positions.
- Every order logged with reason/strategy tag.
- Start with minimum quantity.
- Check current SEBI retail algo-trading rules and Upstox's requirements for API order placement (registration, static IP, order-rate limits) before enabling.

---

## 6. Honest limits (keep in mind)

- **Pine Script**: no tool outside TradingView runs 100% of Pine. PineTS covers a large and growing subset; strategy functions (`strategy.entry` etc.) and some `request.*` calls may not be fully supported. AI conversion + manual review is the reliable path for strategies.
- **Options backtests** need option premium history, not just the index. Index-only backtests give direction signals, not real option P&L. Plan for storing option candles from now on, and check what expired-contract data Upstox provides.
- **Drawing tools** in Lightweight Charts are built by us via plugins, not included out of the box.
- **Backtest ≠ live**: always do out-of-sample + paper before real money.

---

## 7. Rules for Cursor

1. Work one phase at a time. Small commits, each runnable.
2. Never put API keys or tokens in code — `.env` only.
3. Type hints in Python, strict TypeScript in frontend.
4. Write tests for: candle builder, resampler, cost model, backtest engine (especially no-look-ahead).
5. Never place a real order unless Phase 7 is explicitly enabled and `LIVE_TRADING=true` in `.env`.
6. If unsure about an Upstox endpoint, read the official docs — do not guess.
7. After each phase, update a `PROGRESS.md` with what was done and what's next.
8. Testing: pytest (backend), Vitest (frontend). Critical modules
   (backtest engine, cost model, candle builder, resampler, risk manager)
   need tests written BEFORE the code. Upstox calls are mocked; tests never
   hit the live API or place orders. Run all tests after every change.
   A phase is complete only when all tests pass.
