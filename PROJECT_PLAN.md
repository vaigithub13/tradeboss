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

**Auto-analyse.** `AI_ANALYSIS_AUTO` defaults off. When it is on, the app asks for an analysis every `AI_ANALYSIS_AUTO_MINUTES` (default 15) during an open NSE session. The clock alone is not enough: a weekday on the NSE holiday calendar does not run, and `market_info` closed does not run. `market_info` open does run, including a budget weekend and a Muhurat session outside 09:15–15:30. With no market_info yet, a weekday that is not a holiday, or a known full weekend session, runs from 09:15 until 15:30 IST. The first run waits one full interval after the app opens. Each run uses the same request, including the replay cursor, and the panel shows the same token and dollar line.

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

## Drawings and fair value gaps

Built. The tests in `frontend/src/draw/model.test.ts`, `backend/tests/test_drawings.py`, and `backend/tests/test_fvg.py` are the contract. The left toolbar draws on the candlestick series with a Lightweight Charts v5 primitive. Fair value gaps are `fvg` in the indicator registry, and the chart draws the boxes from `POST /api/fvg`.

### Manual drawings

A left toolbar, in this order: cursor, trend line, ray, extended line, horizontal line, horizontal ray, vertical line, rectangle, Fibonacci retracement, text, price/time measure, long position, short position, eraser, lock all, hide all. Cursor, eraser, lock all, and hide all are not stored drawings.

An anchor is `{ time, price }`. `time` is unix seconds. Nothing is stored in pixels. Zoom, scroll, a new candle, and a restart do not move an anchor. Switching timeframe does not rewrite the stored time. On screen, the anchor is drawn on the bar that contains that time. Intraday bars use the same 09:15 IST buckets as `resample`. A 12:47 anchor on a 15-minute chart is drawn at the 12:45 bar (unix `1790838900` on 1 Oct 2026) and the stored time stays 12:47 (`1790839020`). A 10:20 anchor on a 1-hour chart is drawn at 10:15. A daily bar maps to 09:15 IST of that date. A weekly bar maps to 09:15 IST on that week's Monday.

The empty area to the right of the last candle is drawable. A whitespace series on the same time scale carries future bar-start times (no price, no price line, not in the legend). Those times follow the NSE session: 09:15–15:30 buckets, and they skip the night, the weekend, and holidays from the NSE calendar. An anchor on the next 15-minute slot after `1790838900` (12:45 on 1 Oct 2026) is `1790839800`. The slot after 15:15 that day is Monday 5 Oct 09:15 (`1791171900`), because 2 Oct 2026 is a holiday. It is not pulled back onto the last candle. When that candle arrives, the stored time is already the candle's start, so the line lands on it.

Magnet snaps the price to the nearest of that bar's open, high, low, and close. An equal distance keeps the earlier of open, high, low, close.

Two anchors: trend line, ray, extended line, rectangle, Fibonacci, measure. One anchor: horizontal line, horizontal ray, vertical line, text. A ray extends past the second anchor. An extended line extends both ways. A horizontal ray extends right. Extend left and extend right are properties and can be changed. Other properties: colour, width, line style (`solid`, `dashed`, `dotted`), and a rectangle fill colour. No fill is `null`. Text keeps a string.

Fibonacci prices run from the second anchor back toward the first at 0, 0.236, 0.382, 0.5, 0.618, 0.786, and 1. Measure reports the price change, the percent of the first price, the seconds between the two times, and the number of chart-bar steps between the two bar starts.

Select, drag a handle, and move the whole drawing. Delete removes the selected drawing (the Delete key). Lock all refuses move, handle edits, and delete. Hide all leaves the drawings saved and draws none. Undo and redo are a document stack (Cmd+Z, Cmd+Shift+Z). A new edit after undo drops the redo branch.

Drawings are per symbol, not per timeframe, in SQLite at `data/drawings.sqlite` (the `data/` directory stays gitignored). `GET /api/drawings?symbol=` loads them. `PUT /api/drawings` replaces that symbol's list. Export is `{ "symbol", "drawings" }`. Import replaces that symbol and leaves every other symbol alone. A missing symbol, an unknown tool, or an anchor without `time` and `price` is rejected. The allowed tools are `trend`, `ray`, `extended`, `horizontal`, `horizontal_ray`, `vertical`, `rectangle`, `fib`, `text`, `measure`, `long_position`, and `short_position`.

Each drawing has `knownAt`. A drawing made during replay is stamped with the replay cursor, so it stays visible at that cursor. A drawing made live is stamped with the last candle's time. While a replay cursor is set, a drawing whose `knownAt` is after that cursor is not drawn. A later live drawing is hidden in an earlier replay. With no cursor, age does not hide anything. New live candles do not change stored anchors. The lines are a Lightweight Charts v5 series primitive on the candlestick series, so they move with the scale.

Each drawing also stores `drawnOn` (the timeframe on screen when it was created; empty on older rows), `showOn` (the timeframes it is drawn on; `null` means every timeframe, which is the default), and per-drawing `hidden` and `locked`. A missing `showOn` still means every timeframe. Lock all and a locked drawing both refuse a move and a delete. Hide, lock, and `showOn` can still be changed from the object tree.

The object tree lists every drawing for the symbol: tool, the timeframe it was drawn on, and `knownAt`. Select, hide, lock, delete, and zoom to work from that list when the drawing is on another timeframe or only a few pixels wide. Zoom to puts both anchor times in the visible range, including two intraday times that land on the same daily bar.

A drawing whose screen width is under 10px draws its lines and no text. Fibonacci keeps a label only when it sits at least 12px from the previous label. A segment or box shorter than 8px is hit-tested as 8px, so a Fibonacci that collapses onto one daily bar can still be selected and erased.

### Automatic fair value gap

`fvg_boxes` in `app.indicators.fvg` is the function the chart and a backtest both call. `fvg` is also a registry type (`validate_params` / `compute`), pane `price`, so a strategy goes through the same indicator hub as EMA. The boxes are drawn with a series primitive, not a line series.

Three consecutive candles. Candle 3 is the bar that has just closed. Bullish: `low` of candle 3 is strictly above `high` of candle 1. The box bottom is candle 1's high and the top is candle 3's low. Bearish: `high` of candle 3 is strictly below `low` of candle 1. The box top is candle 1's low and the bottom is candle 3's high. The box starts at candle 1 and runs right. It does not exist on candle 1 or candle 2. A prefix that ends before candle 3 has no box. Adding later bars does not change the top or the bottom, and does not fill a formation value onto an earlier bar.

Mitigation looks only at bars after candle 3. `touch`: a bullish bar whose low is at or below the top, or a bearish bar whose high is at or above the bottom. `half`: price reaches the midpoint (bullish low at or below it, bearish high at or above it). `full`: price trades through the far side (bullish low at or below the bottom, bearish high at or above the top). The default is `touch`. `when_mitigated` is `stop` or `fade`. Stop ends the box on the mitigation bar. Fade keeps the box extending and marks it faded. An open box extends and is not faded.

`min_gap` defaults to 0. `min_gap_mode` is `points` or `percent`. Points compare the gap to `min_gap`. Percent compares `gap / close of candle 3 * 100` to `min_gap`. A gap equal to the minimum is kept. `show_last` defaults to 10 and keeps the most recently formed boxes. `timeframe` defaults to empty, which means the bars passed in. The chart asks for that timeframe's candles and then calls `fvg_boxes`. The function does not resample.

A higher-timeframe gap is drawn on a lower chart only after candle 3 of the higher timeframe has closed. `fvg_boxes` drops a box when `bar_seconds` is set and `formed time + bar_seconds` is still after `as_of`. The chart's `as_of` is the replay cursor, or the last bar's close once that bar has finished, or the last bar's start while it is still forming.

`session_gaps` is `include` (the default) or `exclude`. Exclude drops a gap whose candle 1 and candle 3 fall on different IST dates (an overnight gap). The indicator setting is labelled Overnight gaps.

`compute` arrays `bull_top`, `bull_bottom`, `bear_top`, and `bear_bottom` are set on candle 3 only, and are NaN before that. `show_last` dropping an older box clears that bar too.

### Long position and short position

The tests in `frontend/src/draw/position.test.ts`, `backend/tests/test_drawings.py`, and `backend/tests/test_position_option.py` are the contract.

Both tools are stored drawings. A click sets the entry anchor `{ time, price }`. The other two anchors are the target and the stop. Their time is the right edge. Their prices are the default levels. Anchors stay time and price. `knownAt`, `drawnOn`, `showOn`, hide, lock, undo, the object tree, zoom to, and the 8px hit area are the same rules as every other drawing. The object tree labels them "Long position" and "Short position".

The default stop is 1% of the entry price away from the entry. The default target is twice that far, so the risk/reward ratio is 2. A long target is above the entry and its stop is below. A short target is below the entry and its stop is above. The right edge is 15 session bars after the entry bar. The count uses the same NSE session rules as the empty area to the right of the last candle, including holidays. An entry at 09:15 IST on 1 Oct 2026 on a 15-minute chart ends at 13:00 the same day (`1790839800`). An entry at 15:15 that day (`1790847900`) ends at 12:45 on Monday 5 Oct (`1791184500`), because 2 Oct 2026 is a holiday.

Four handles. Entry moves the entry time and price. Target moves the target price only. Stop moves the stop price only. The right edge moves the target time and the stop time together and leaves both prices where they are. Lock all and a locked drawing refuse those handle edits and refuse delete. Account size, risk, colours, compact mode, the options toggle, hide, lock, and `showOn` can still be changed.

The profit zone is the band from entry to target, green `#089981`. The risk zone is the band from entry to stop, red `#f23645`. A short swaps which band is above the entry. Those two colours are settings. Each zone is filled at about 20% opacity with a thin border, and a thin line marks the entry. Drag handles show only while the drawing is selected or hovered.

Reward points are `target − entry` for a long and `entry − target` for a short. Risk points are `entry − stop` for a long and `stop − entry` for a short. Each percent is that distance divided by the entry price, times 100. The ratio is reward divided by risk. A risk of 0 or less has no ratio. The risk budget is `accountSize × riskPercent / 100` when risk mode is `percent`, and `riskRupees` when risk mode is `rupees`. The default account size is ₹10,00,000, the default risk is 1%, and the default rupee risk is ₹10,000. Quantity is whole lots: `floor(budget / (risk points × lot size))`. Units are lots times the lot size. Rupee profit is reward points times units. Rupee loss is risk points times units. A missing lot, a lot below 1, or a risk that is not positive produces 0 lots. The lot is the dated lot table for the symbol on the entry's IST date (`NIFTY50` uses `NIFTY`). A date inside a lot-size transition is ambiguous: the drawing keeps the lot empty and does not guess. A stored lot size overrides the table. Prices are what is stored. Points mode edits the target and the stop as a distance from the entry and writes the price back.

Labels are filled rounded boxes with white text, centred on the tool. A long puts the target box above the profit zone and the stop box below the risk zone. A short puts each box on the outer side of its zone. The centre box sits on the entry line. Boxes shift or sit beside each other when they would overlap. Numbers use two decimals with trailing zeros dropped, so a ratio of 2 prints `2` and 2.5 prints `2.5`. Ticks are points divided by 0.05. Amount is points times lots times the lot size, in rupees, with no thousands separator.

- target `Target: 200 (0.83%) 4000, Amount: 13000` for a long from 24000 to 24200 with a 23900 stop, one lot of 65, and a ₹10,000 budget
- stop `Stop: 100 (0.42%) 2000, Amount: 6500`
- centre, while open, `Open PnL: 5200, Qty: 1` and `Risk/reward ratio: 2`

The centre box is the profit colour when the result is not a loss, and the stop colour when it is. After a target or a stop it says `Closed PnL`. Before the entry trades it says `Pending`. If the right edge passes first it says `Not entered`. An ambiguous bar adds `stop assumed`. Compact mode keeps only the centre box, with quantity and the risk/reward ratio. When the quantity rounds down to 0 lots, the centre box shows `Qty: 0` and `risk/lot Rs X > budget Rs Y`, and the target and stop amounts are for one lot, labelled `per lot`.

The position stays pending until a bar at or after the entry time actually trades the entry price. A target or stop printed before that trade does not count. If the right edge is reached, or the replay cursor is at or past the right edge, and the entry still has not traded, the outcome is `not entered` and the rupee result is empty. After the entry trades, the tool reads the candles it is given. Bars that start before the entry time, bars that start after the right edge, and bars that start after the replay cursor are ignored. While none of the remaining bars has reached the target or the stop, the position is open: the zones run to the last bar still inside the right edge, and the open result uses that bar's close. A long hits the target when a bar's high is at or above it, and hits the stop when a bar's low is at or below it. A short hits the target on the low and the stop on the high. The first such bar ends the zones on that bar. A fill at the target takes the reward. A fill at the stop takes the loss. Later bars do not change it. Live updates by passing the new candles into the same function.

When one bar reaches both prices, the 1-minute bars inside it decide. Only minutes at or after the entry time, inside that bar, and at or before the cursor are read. The first minute that touches one price wins, and the zones end on that minute. A minute that touches both, or a chart bar that touches both when no 1-minute bar separates them, is `ambiguous (stop assumed)`: the stop fill is used. Replay passes the cursor in. A target that prints on a later bar stays hidden, and the result stays the open result of the last bar the cursor can see.

The drawing stores a `position` object. Defaults: account size 1000000, risk mode `percent`, risk percent 1, risk rupees 10000, lot size `null`, price mode `price`, profit colour `#089981`, stop colour `#f23645`, compact false, options false. A long or short position has exactly three anchors. A short list is rejected with a message that contains `3 anchors`. `riskMode` is `percent` or `rupees`, and anything else is rejected with a message that contains `riskMode`. `priceMode` is `price` or `points`. `lotSize` is null or a positive whole number, and a fraction is rejected with a message that contains `lotSize`. A missing `position` object is stored as the defaults. Another tool does not grow a `position` field. `riskMode` is `percent` or `rupees`. `priceMode` is `price` or `points`. `lotSize` is null or a positive whole number.

The options section is off unless the drawing's options toggle is on and the symbol is `NIFTY50` or `NIFTY`. It then shows the ATM call for a long, or the ATM put for a short, of the nearest weekly expiry, from the same contract choice the backtest uses. Entry, target, and stop premiums are each option model v1 repriced at that index price and at the time that level is reached (the right-edge time when it has not been reached), so the move includes gamma and time decay. The India VIX is the stored 1-minute VIX at or before the entry, and during replay at or before the cursor. A missing VIX, a cursor before the entry, a pending position, or a position that was not entered leaves the section out. One lot is bought at the entry premium and sold at the exit premium. An open position exits at the model premium of the index close at that bar's time. A target exits at the target premium. A stop, including an ambiguous stop, exits at the stop premium. Both legs use the options cost table. The rupee result is one lot after those costs. With the toggle on, the centre box adds one line, `ATM CE ~{entry} -> T {target} / S {stop}, option qty {n}` (a short uses PE). The quantity is the option-based lot count: premium lost at the stop per lot, plus the buy and the stop-exit costs, divided into the same risk budget. The centre box tooltip says `estimated`. This path does not call Upstox.

---

## 6. Honest limits (keep in mind)

- **Pine Script**: no tool outside TradingView runs 100% of Pine. PineTS covers a large and growing subset; strategy functions (`strategy.entry` etc.) and some `request.*` calls may not be fully supported. AI conversion + manual review is the reliable path for strategies.
- **Options backtests** need option premium history, not just the index. Index-only backtests give direction signals, not real option P&L. Plan for storing option candles from now on, and check what expired-contract data Upstox provides.
- **Drawing tools** are the Drawings section above: our own series primitives, not a built-in Lightweight Charts toolbar.
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
