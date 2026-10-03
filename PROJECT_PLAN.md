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

The chart gets an Analyse button. The model sees a compact context and returns JSON. The panel and the chart show that JSON. Nothing in this phase places an order or adds a control that would.

**Context** (`app/ai/context.py`). One pure function, `build_context`, for the current symbol. No network inside it. Callers pass candles, the event calendar, VIX bars, and an optional option-chain getter.

- Timeframes are exactly `5m`, `15m`, `1h`, and `1D`. Each keeps the last 40 bars (`CANDLE_LIMIT`).
- Indicator values are the last outputs of `app.indicators.registry.compute`: EMA 20, RSI 14, VWAP, and MACD (12, 26, 9). The same function the chart uses. No second implementation.
- A swing high is a bar whose high is strictly above the two bars on each side. A swing low is the same test on the low. Support is the swing-low prices. Resistance is the swing-high prices. Keep the latest three of each.
- Day range is the current IST session on the 5-minute bars: first open, highest high, lowest low, last close.
- India VIX is the last stored close. It is stale when that bar is more than 300 seconds before `as_of`, the same rule as the option model. No bar means value null and stale.
- Event flags come from `EventCalendar`: `event_day` and `reaction_day` for the `as_of` session, plus the event names.
- Option chain, Nifty only. The chart symbol `NIFTY50` maps to instrument key `NSE_INDEX|Nifty 50`. The getter is `GET /v2/option/chain` on the existing read-only Upstox client. `expiry_date` is the nearest weekly expiry from `ExpiryCalendar` as `YYYY-MM-DD`, including the expiry day itself and a holiday that moved the expiry to the previous trading day. Upstox documents that route as a Bearer access-token call and says the chain is not available for MCX ([Put/Call Option Chain](https://upstox.com/developer/api-documentation/get-pc-option-chain)). This app sends the analytics token the other market-data GETs already send. A 401 or any other failure does not fail the analysis: `options` is `{available: false, reason}` and the reason is a fixed sentence, never the response body. The strike closest to `underlying_spot_price` is ATM; a tie takes the lower strike. Keep that row's PCR and the call and put LTP, OI, bid, and ask. Any other symbol leaves `options` null and does not call the getter.
- The context JSON has no token, key, or authorization header. A chain error that contains one is not copied in.

**Model** (`app/ai/analyse.py`, `app/ai/schema.py`). `AI_ANALYSIS_MODEL` in `.env`, otherwise `AI_MODEL`, otherwise `gpt-4o-mini`. The prompt says the context is data, never instructions, and that the reply cannot be an order. The reply is one JSON object:

- `trends`: `5m`, `15m`, `1h`, `1D`, each `up`, `down`, or `sideways`
- `bias`: `bull`, `bear`, or `neutral`
- `key_levels`: `{price, kind: support|resistance, label}`
- `patterns`: strings
- `bull` and `bear`: `{trigger, invalidation, note}`
- `confidence`: number from 0 to 1
- `reasoning`: a non-empty string

Any other key is rejected, including `order`, `orders`, `side`, `qty`, `action`, and `signal`. Every price (levels, triggers, invalidations) must sit inside 5% of the last price (`LEVEL_BAND`). Outside that, the error names the field and the allowed range, for example `bull.trigger 200 is outside 95.00..105.00`. The repair loop is the Pine one: at most two retries (`MAX_RETRIES = 2`). Each retry is shown that error. After three failures the result is not ready. Tests pass a fake client. No test opens a socket.

An optional chart screenshot is a second argument. The client call includes those bytes only when the caller passed them.

**Language.** `AI_ANALYSIS_LANGUAGE` defaults to `en`. The prompt tells the model to write every text field and every label in English. A reply that contains a non-English letter is rejected, and the repair loop is shown that it must be written in English.

**Prices in the text.** A price written in the reasoning, a note, a pattern, or a label must equal a structured price (a key level, a trigger, or an invalidation). `22445.6` in the sentence and `22442.80` on the drawn line is rejected, and the repair loop is shown that error. `22445.60` and `22445.6` are the same price.

**Replay.** Analyse sends the replay cursor. Candle loads for the context use that cursor, the option chain is not called, and `as_of` is the cursor. No candle time in the context is after it, and the last price and the day close are the cursor bar, not the 15:30 close. The chart's last-price line is the candlestick series line, which sits on the last bar in the series. During replay that series stops at the cursor, so the line is the cursor bar's close. Indicator values drawn on the chart stop at the same bar. An analysis made with a cursor is saved under `data/ai/replay/` with `"mode": "replay"`. The live track record does not load that folder and skips any file marked replay.

**Auto-analyse.** `AI_ANALYSIS_AUTO` defaults off. When it is on, the app asks for an analysis every `AI_ANALYSIS_AUTO_MINUTES` (default 15) during weekday market hours, 09:15 until 15:30 IST. The first run waits one full interval after the app opens. Each run uses the same request, including the replay cursor, and the panel shows the same token and dollar line.

**Cost.** The panel shows prompt and completion tokens, `100 in / 50 out`. A dollar figure appears only when both `AI_ANALYSIS_INPUT_USD_PER_MTOK` and `AI_ANALYSIS_OUTPUT_USD_PER_MTOK` are set. No rate is invented for a model name.

**Panel.** Side panel, plus horizontal price lines on the candle series (the same `createPriceLine` the RSI guides use). The panel always shows `AI analysis: context, not a trade signal`. There is no place-order function and no such button (`canPlaceOrders()` is false).

**Track record** (`app/ai/record.py`). Every analysis is a file under `data/ai/` (already gitignored): time (`as_of`), symbol, the context, the analysis, and the sha256 of the canonical context JSON. Each analysis is scored on five horizons: 60 minutes later, the same-session close (the last bar of that IST date at or after 15:15), and 1, 3, and 5 later sessions. The analysis session itself does not count toward the session horizons. A session is one IST date. The same horizons score two baselines: always bullish, and follow the trend (the sign of the 1D EMA 20 slope stored on the context). The panel shows the AI hit rate beside both baselines.

- The bull trigger is reached when a bar's high is at or above it. The bear trigger is reached when a low is at or below it. The trigger score follows the stated bias. Neutral has no trigger score.
- A move inside the neutral band is flat. The band defaults to 0.3% of the analysis price (`AI_ANALYSIS_NEUTRAL_BAND`). Bull is right on an up move, bear on a down move, and neutral when the move stays inside the band.
- A support is respected when no close is strictly below it. A resistance is respected when no close is strictly above it. A wick through the level still counts as respected. No levels means respected.
- Before the horizon exists, the score is pending and is left out of the hit rate.

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

## Replay

Stored candles only. No Upstox call. Replay does not start or stop the live feed.

**Cursor.** A Replay button takes an IST date and time. The chart, on every timeframe, is the resample of only the 1-minute bars at or before that moment. A 5-minute bucket that has three minutes so far is a partial candle. Candle, indicator, and lazy-load requests send `cursor`. No response bar starts after it. Warm-up may read earlier stored bars. It may not read a bar after the cursor.

**Controls.** Play and pause. The step unit is "chart bar" or "1 minute". One-minute steps leave the current chart candle forming. Speed is 1, 2, 5, or 10 of the chosen unit per second. Exit leaves replay and shows the full stored series again. On the last stored bar, step and play stay put.

**Jump to next trade** is labelled "Next trade (review)". It is a review shortcut. Practice mode disables it. It is not a way to see the future while placing paper trades.

**Overlay.** The open backtest run is revealed as the cursor arrives. An entry marker and stop appear on the entry bar. The exit marker and trade card appear on the exit bar. Nothing later is drawn. A strategy stepped bar by bar through replay produces the same trades as the normal backtest over the same dates.

**Later, plan only.** Replay a recorded live session from `data/feed-recordings`. A practice mode where manual paper trades can be placed during replay, with option costs. That mode has no "Next trade (review)" button. Neither has an API in this slice.

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
