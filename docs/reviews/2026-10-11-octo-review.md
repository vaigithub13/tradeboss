# TradeBoss read-only review — 2026-10-11

Branch `main` at `e307dcb`. Read-only: no code or config was changed. `.env`, tokens and `data/` contents were
not read, printed or sent anywhere. No backend, frontend, Upstox or OpenAI call was made.

**How this review was done.** `/octo:auto` (multi-model) is not available in this environment, so this is a
single-reviewer review (Claude Opus 5.5). The "where the models disagreed" section therefore lists judgement calls
instead of model disagreements. Test suites were run locally without writing caches (`PYTHONDONTWRITEBYTECODE=1`,
`-p no:cacheprovider`): backend **1315 passed, 1 skipped, 1 failed**; frontend **355 passed**. The AST-gate bypasses
in C2 were checked by passing source strings to `check_source` (parse only, nothing executed).

## Summary

| # | Severity | Area | Finding |
|---|---|---|---|
| C1 | critical | Safety / Pine | Model-written test code runs through pytest with no AST gate, no env scrub, and before any human approval |
| C2 | high | Safety / Pine | The AST gate can be bypassed (aliased builtins, `typing.get_type_hints`), and the worker can still load `.env` through `app.config` |
| H1 | high | Backtest / look-ahead | `fvg` in the indicator hub applies `show_last` over the whole series, so a bar's value depends on later bars |
| H2 | high | Data integrity / holdout | Ordinary backtests and replays can read the holdout without a refusal or a peek count; a blank End includes it |
| M1 | medium | Paper / test quality | The end-of-day check compares paper signal records of any status with backtest trades by (time, side) only |
| M2 | medium | Reliability / live | Paper Start (an async route) warms up and replays the whole day's recording on the feed's event loop |
| M3 | medium | Paper | A square-off with no quote and no model price leaves the position open and adds a log row on every frame after 15:15 |
| M4 | medium | Test quality | The backend suite is red today: a date-dependent test reads the wall clock |
| M5 | medium | Test quality | Paper-vs-backtest parity and restart tests skip silently without the gitignored `data/` folder |
| L1 | low | Option overlay | A delta-adjusted fill silently falls back to the optimistic minute open when the index 1m bar is missing |
| L2 | low | Option overlay | The premium walk skips the rest of a stop entry's minute and the exit minute; paper checks every tick |
| L3 | low | Paper | The paper `_Ctx` lacks `time`, raises on `indicator`, and fixes `open_orders=[]` and `lot_size=1` |
| L4 | low | Paper | A reversal whose exit leg cannot be priced leaves `_side` flipped while the book still holds the old option |
| L5 | low | Paper | A bar given up after a feed gap is never shown to the strategy, so its state differs for the rest of the day |
| L6 | low | Calendar | The holiday table ends 2026-12-25; later dates silently count as trading days (expiry, DTE, T) |
| L7 | low | Backtest | `vwap_fut` cannot be used from a strategy (the frame has no `fut_volume`), and the lookahead test omits `stoch`, `fvg` and `vwap_fut` |
| L8 | low | Replay | The 1m bar that *starts* at the cursor is shown with its full OHLC, up to 59 s past the cursor |
| L9 | low | Frontend / replay | The replay overlay never draws the stop (`stop: null`), although the spec says it does |
| L10 | low | Safety | A path in a request is used before validation (`_smoke_draft` name; the catalog's `user:` slug) |
| L11 | low | Data integrity | Some writes are not atomic: AI track record, saved strategy, spread report |
| L12 | low | Option overlay | Premium exit levels round to 0.01, not to the 0.05 tick |
| L13 | low | Reliability | The feed lock is released between reconnects; `/api/live/ws` has no Origin check |

---

## Findings

### C1 — critical — Model-written tests run outside every gate

**Where.** `backend/app/pine/convert.py:127-149` (`_tests_run`), called from `convert.py:202-204` during
`POST /api/pine/convert`.

```python
test_path.write_text(header + "\n" + tests)
completed = subprocess.run(
    [sys.executable, "-m", "pytest", str(test_path), "-q", "--tb=no"],
    cwd=str(BACKEND),
    capture_output=True, text=True, timeout=30,
)
```

`tests` is the `tests` field of the model's JSON reply. It never goes through `check_source` (only `python` does,
in `_problems`). The subprocess gets no `env=`, so it inherits the backend's environment, unlike the worker
(`scrubbed_env`). Its cwd is `backend/`, one directory below `.env`. This happens inside Convert, before you see or
approve anything. The pasted Pine script is untrusted text, sent to the model as data. A script carrying a prompt
injection, or simply a bad reply, gets arbitrary Python executed as your user.

**Why it matters.** The only broker credential and the OpenAI key sit in `.env`, readable by that process. This is
the one path where code nobody reviewed runs with full privileges. It is also the cheapest of the findings to close.

**How to confirm.** Add a test with a fake `DraftClient` whose reply is
`{"python": <valid strategy>, "tests": "import pathlib; pathlib.Path(r'<tmp>/pwned').write_text('x')\ndef test_ok(): pass"}`.
Call `convert_draft` and assert that `<tmp>/pwned` does not exist. Today it is created.

**Suggested fix.** Do not execute generated tests. If they are worth keeping, run them in the same worker with the
same AST gate (`check_source` on the test module, extended to allow `pytest` only), the scrubbed env, a throwaway
cwd, and no `app.*` import beyond the allow-list. Otherwise drop the `tests` field and rely on `_automatic` and
`smoke_strategy`.

### C2 — high — The AST gate can be bypassed, and the worker can still load secrets

**Where.** `backend/app/pine/sandbox.py:114-118` (`visit_Call`), `sandbox.py:106-109` (any `typing` name), and
`backend/app/pine/isolated.py:36-42` (worker launch).

```python
def visit_Call(self, node: ast.Call) -> None:
    func = node.func
    if isinstance(func, ast.Name) and func.id in _BANNED_CALLS:   # only a direct call by name
```

Checked with `check_source` (parse only):

| source | result |
|---|---|
| `f = open` at module level, then `f('x')` in `on_bar` | **passes** |
| `g = getattr`, then `g(ctx, '_' + '_class__')` | **passes** (the dunder is built from a string, so the attribute check never sees it) |
| `from typing import get_type_hints` plus `def h(x: '<expr>')`, then `get_type_hints(h)` | **passes** (`get_type_hints` calls `eval` on string annotations, and `from __future__ import annotations` makes every annotation a string) |
| `open('x')` called directly | blocked |

The worker runs `python -m app.pine.worker` with `cwd=BACKEND_ROOT`, so the whole `app` package can be imported.
`app.config.settings` reads `.env` from disk through pydantic-settings, whatever the environment says.
`scrubbed_env` removes the secrets from the environment only. They are one import (or one `open`) away, and the
worker has network access.

**Why it matters.** CLAUDE.md promises that user strategies "only run in the isolated worker with no secrets" and
that the gate "cannot be bypassed". Neither holds against a hostile draft. The draft runs in the worker during
Convert (`_automatic`, `smoke_strategy`), before Approve, so the human diff review is not the barrier it looks like.

**How to confirm.** Add the three sources above to `tests/test_pine_gates.py` (or a similar file) as cases that
must raise `SandboxError`. All three fail today. Add a worker test that loads an allowed strategy whose `on_start`
tries `import app.config`; it should fail.

**Suggested fix.**
1. Reject any bare reference to a banned name, not only calls: check `ast.Name` in `Load` context against
   `_BANNED_CALLS | {"type", "object", "super" outside __init__, "__builtins__"}`.
2. Replace "any `typing` name" with an explicit list (`Any`, `Optional`, `List`, `Dict`, …) that excludes
   `get_type_hints`, `ForwardRef`, `TYPE_CHECKING`-style tricks, and `cast`.
3. Ban string-valued annotations that are not plain dotted names.
4. Make the worker unable to read secrets even after a bypass: run it from a temporary directory with a minimal
   `PYTHONPATH` that holds only `contracts`, `pine_common` and the worker. Better still, use macOS `sandbox-exec`
   (or a separate user) to deny network access and reads outside that directory.

### H1 — high — `fvg` leaks the future through `show_last` in backtests

**Where.** `backend/app/indicators/fvg.py:175-176` and `:187`, reached through `registry.py:199-200` and
`backtest/context.py:186`.

```python
if show_last >= 0:
    boxes = boxes[-show_last:]          # the last N boxes of the WHOLE frame
...
for box in fvg_boxes(df, params):       # compute_fvg: values only on those boxes' candle 3
```

`IndicatorHub` runs `registry.compute` once over the full series, then reveals one value per bar. That is safe only
when value[i] equals compute(bars[:i+1])[i]. For `fvg` it does not hold: with the default `show_last=10`, a gap
formed at bar i reads NaN in the backtest if ten more gaps form later. A strategy therefore sees gaps only near the
end of the run. Whether a value exists at all depends on the future.

**Why it matters.** PROJECT_PLAN says `fvg` is a registry type "so a strategy goes through the same indicator hub
as EMA". No shipped strategy uses it yet, so no published result is affected. The first FVG strategy, hand-written
or converted, would be silently wrong, and wrong in a way that depends on where the run ends.

**How to confirm.** Add `("fvg", {})` (and `stoch`, below) to the `specs` list in
`tests/test_bt_lookahead.py::test_1d_ctx_indicator_values_equal_the_chart_code_run_on_the_past_only` (lines
194-202), on data with more than ten gaps. It fails for `fvg`.

**Suggested fix.** Keep `show_last` as a drawing setting only. In `compute_fvg` (the registry path), ignore
`show_last`, or apply it causally (the last N boxes formed at or before each bar, which the per-bar formation arrays
already are). Add every registry type to test 1d so a new indicator cannot skip the check.

### H2 — high — The holdout can be read by ordinary runs, uncounted

**Where.** `backend/app/routes/backtests.py:97-105` → `JobService.start` → `catalog.parse_config`
(`catalog.py:189-194` checks only that `end >= start`). Walk-forward enforces the holdout
(`walkforward.py:296-301`, `holdout.assert_research_range`); a normal run and `POST /backtests/{id}/replay` do not.

```python
start = _date("start", body.get("start"), required=True)
end = _date("end", body.get("end"), required=False)
...
if end is not None and end < start:
    raise RunRequestError("end is before start")
```

Only the frontend warns. A blank End, which is the form's default, runs through the last stored bar, which
includes 2026-07-01..2026-10-01. Those runs do not increment `holdout_peeks`, so "final holdout has been run N
times" undercounts. PROGRESS already records research runs that overlapped the holdout (the 2026-10-03 parity
window).

**Why it matters.** The holdout is the one clean out-of-sample test before real money. Looking at it without
counting turns it into research data while the result still claims a peek count of 0.

**How to confirm.** A test that posts `{"strategy": "ema_cross", "start": "2026-06-01", "end": null, ...}` to
`/api/backtests` and asserts either a 400, or that `holdout_peeks` went up by one. Today neither happens.

**Suggested fix.** On the backend, refuse a run that intersects the holdout unless the request carries an explicit
`holdout_ack: true`. Count that as a peek, and stamp the result `touches_holdout`. Clamp a blank End to the
research end unless `include_forward` is set. The warm-up reading holdout bars before a forward-period start is
state only and can stay, but say so in the result.

### M1 — medium — The end-of-day check can say "no differences" when paper did not trade

**Where.** `backend/app/paper/live.py:205-206` and `backend/app/paper/check.py:68-69`.

```python
entries = [{"time": r["time"], "side": r["side"]} for r in s.signals
           if r["side"] in ("BUY", "SELL") and r["time"] is not None]       # any status
...
lk = {(e["time"], e["side"]) for e in live}
bk = {(e["time"], e["side"]) for e in backtest}                              # backtest TRADES
```

Paper signal records include `unfilled` (no quote and no model price; contract missing from the master),
`skipped` ("already in that position") and "after the 15:15 square-off". The backtest side is filled trades. An
unfilled paper signal at the same bar as a backtest trade matches, so the check reports agreement while paper made
nothing. Contract, fill price, exit time and exit reason are not compared at all. The daily parity in PROGRESS
(contract, entry and exit minute, reason, net) was done by hand.

**Why it matters.** The forward test is the evidence for going live. The automated check that appears in every day
file should not be able to pass while P&L diverges.

**How to confirm.** A unit test that calls `compare_signals` with a live record `{"status": "unfilled"}` at 09:20 BUY
and a backtest trade at 09:20 BUY, and expects one difference. Today it returns `[]`.

**Suggested fix.** Compare filled entries only, and list unfilled and skipped records apart. Extend the check to
per-trade rows (contract, entry minute, exit minute, exit reason, net), using the trade report both sides already
produce, with a tolerance for price.

### M2 — medium — Paper Start blocks the live event loop

**Where.** `backend/app/routes/paper.py:78-86` (`async def start` calls `runner.start(...)` directly) and
`backend/app/paper/live.py:146-161` (`_make` → `stored_warmup`, then `_catch_up` → `replay_into` over the day's
recording). `LiveService._on_frame` runs on the same loop.

```python
@router.post("/start")
async def start(body: StartBody, request: Request) -> dict[str, Any]:
    ...
    runner.start(day, body.strategy, body.params, now_ms=now)
```

A Start or a restart at 13:00 replays every recorded frame since 09:00 (164k frames on 5 Oct for the full day)
synchronously, plus a DuckDB read and a resample for the warm-up. While that runs, no frame is processed, the hub
pushes nothing (the browser's 6 s watchdog fires), the 15:15 square-off and stop fills wait, and the websocket
buffer grows. `service.start()` resumes up to four slots one after another before the feed task starts.

**Why it matters.** It is a market-hours action that degrades the live pipeline for every slot, and it is
invisible in tests that use small recordings.

**How to confirm.** A test that times `runner.start` against a synthetic recording of about 100k frames, or one
that asserts a live frame scheduled on the loop during a Start is processed within 1 s.

**Suggested fix.** Build and catch up the session in a worker thread (`asyncio.to_thread`) on a private engine and
book. Then, on the loop, swap it in and feed it the frames that arrived meanwhile (buffer them per runner while it
starts).

### M3 — medium — A failed square-off repeats every frame and stays open

**Where.** `backend/app/paper/session.py:209-220` (`on_clock`) and `:513-526` (`_square_off`).

On a frame at or after 15:15 with a position open, `_square_off` is called. If `_fill` returns None (no fresh
quote, and `model_price` returns None, for example with no VIX), it appends an `EXIT … unfilled` signal and returns.
The next frame does the same, so the day file gains one row per frame until 15:30, and the position is still open
in the book at the end of the day.

**Why it matters.** It is rare (the model fallback covers most cases), but when it happens the day file balloons and
the week counts a position that never closed.

**How to confirm.** A `PaperSession` test with an empty `QuoteBook`, `model_price` returning None, an open position,
and ten `on_clock` calls after 15:15. Today it records ten unfilled exits.

**Suggested fix.** Record the failed square-off once, retry silently, and at `end_of_day` close at the last known
quote or model price with a `forced` flag, so the day never ends holding a position.

### M4 — medium — The backend suite fails today (clock-dependent test)

**Where.** `backend/tests/test_upstox_api.py:165-170` against `backend/app/routes/upstox.py:230`.

```
assert 85 <= (date(2026, 10, 3) - first_call_from).days <= 95
E       assert 85 <= 82
```

The test fakes the time as 2026-10-03, but `history_sync` computes its default start from
`datetime.now(IST).date()`. Run on 2026-10-11, the start is 2026-07-13. The test passed when written and broke once
the real date moved about five days on.

**Why it matters.** CLAUDE.md says a step is complete only when all tests pass. A permanently red suite hides real
failures.

**How to confirm.** It fails now (`1 failed, 1315 passed, 1 skipped`).

**Suggested fix.** Inject the IST "today" into the sync route the same way the other Upstox routes accept `today`,
and pass the fake date from `use_fake`.

### M5 — medium — Parity and restart tests depend on gitignored data

**Where.** `backend/tests/test_paper_replay.py` (`SKIP5`/`SKIP6`/`SKIP7` and `pytest.skip(missing)`),
`test_paper_restart.py:24` (module-level `skipif`), `test_bt_live_timing.py:148-153`, plus the real-data branches
in `test_indicators_registry.py`, `test_indicators_chunks.py` and `test_candle_pages.py`.

On this machine they ran (only test 7d skipped). On a clean checkout, CI, or after `data/` moves, the whole
paper-vs-backtest proof (5–9 Oct replays, restart resume, live timing at 09:26) skips without failing.

**Why it matters.** These are the tests that guard the most important property of the forward test: that paper
equals the backtest.

**How to confirm.** Run `uv run pytest -rs` with `data/` renamed (in a worktree); count the skips.

**Suggested fix.** Commit small, de-identified fixtures: one day's index 1m bars, VIX, an option tape for two
contracts, and a trimmed feed recording without depth beyond ATM. Keep the real-data tests as an extra layer, and
make a missing fixture a failure, not a skip.

### L1 — low — Delta-adjusted fill silently degrades to optimistic

`backend/app/options/model.py:226-230` (`_index_open`), `:565-569`, `:276-277`.

```python
if delta is None or index_trigger is None or index_open is None:
    return float(open_)
```

When the index 1m bar at the fill minute is missing, the stop fill is priced at the option's minute open (the
optimistic mode) with no flag, although the run says `delta_adjusted`. To confirm: an overlay test whose index bars
lack the entry minute, asserting a flag or a warning. Fix: flag `delta_fallback` on the trade, count it in the
warnings, or use the nearest earlier index minute.

### L2 — low — The premium walk skips minutes paper checks

`backend/app/options/model.py:605` starts the walk at `entry_time + 60` for an entry inside the minute, and the
walk covers `[start, exit_time)`, so the exit minute is skipped too. Paper's `on_option_tick` checks every traded
price from the fill on. A −20% or +40% move inside the rest of a breakout minute is unusual but possible, mostly on
expiry day. To confirm: a tape where the entry minute's low after the fill is below the stop. Fix: walk the entry
minute's range from the fill price (flag it ambiguous, stop first), and the exit minute up to the index exit.

### L3 — low — Paper context differs from the backtest context

`backend/app/paper/session.py:64-83`: no `time` attribute, `indicator()` raises `NotImplementedError`,
`open_orders=[]`, `lot_size=1`, `cash=0.0`. `STRATEGY_CONTEXT_ATTRS` includes `time`, and the worker-parity test
does not cover the paper context. Log XZ and Price Channel (the only paper strategies) read none of these, so nothing
is wrong today. A strategy that reads them would behave differently live. To confirm:
`missing_context_attributes(_Ctx(...))` returns `['time']`. Fix: give paper's context the same fields (a
`PastSeries`-backed indicator hub computed incrementally, the working stops as `open_orders`), and assert
`missing_context_attributes` is empty in a test.

### L4 — low — A half-priced reversal desynchronises paper

`backend/app/paper/session.py:400-420`. `_step` sets `_side` to the new direction before `_act`. In `_enter`, if the
new option's entry is priced but the old position's exit is not, it returns with
`"could not close the open position"`: the strategy thinks it reversed while the book still holds the old option. To
confirm: a quote book where only the new contract has a quote. Fix: decide `_side` from the book's outcome, or close
first and open only after the close succeeds.

### L5 — low — Given-up bars desync the strategy for the rest of the day

`backend/app/paper/bars.py:106-112`. A bar whose gap never fills within 180 s is skipped for good. The strategy's
history then lacks a bar that the end-of-day backtest has, so recursive state (Log XZ's RMA) differs for the rest of
the session. This is documented and logged. Suggested: when the official or backfilled minutes arrive later, rebuild
the strategy state from warm-up plus all complete bars, without trading the past, so later decisions match again.

### L6 — low — Expiry calendar past 2026

`backend/app/backtest/data/nse_holidays.json` runs from 2022-01-26 to 2026-12-25. `expiry.py:91` treats any date
not listed as a trading day. From late December, paper's nearest-weekly choice, DTE and T for 2027 dates may be
wrong without warning. Fix: raise (or warn loudly) when a date is after the last year in the table, and add 2027
when NSE publishes it.

### L7 — low — Indicator coverage gaps

`registry.compute("vwap_fut")` raises `VolumeRequired` in a backtest because `candles_to_frame` has no
`fut_volume` column (`registry.py:195-198`). A strategy cannot use the futures VWAP, and only a fallback count is
reported (`engine.py:244-262`). Test 1d covers sma, ema, bb, supertrend, rsi, macd and vwap, but not `stoch`,
`fvg`, `vwap_fut` or `atr` (H1 shows why that matters). Fix: add the column in the hub for NIFTY50, or refuse
`vwap_fut` with a clear error, and add every type to test 1d.

### L8 — low — The replay cursor includes the minute that starts at it

`backend/app/backtest/engine.py:301` (`c["time"] <= cursor`) and `data/service.py:107-132`. The spec says "bars at
or before that moment", so a bar starting at the cursor is shown complete, and its close is up to 59 s later than the
cursor. This only matters if the cursor is read as "now" (practice mode, AI replay analysis). Decide which it means
and document it. If it means "now", clip to `time + 60 <= cursor`.

### L9 — low — The replay overlay never shows the stop

`frontend/src/App.tsx:157` passes `stop: null` for every trade into `reveal()`, so `replayReveal.stops` is always
empty. PROJECT_PLAN Replay: "An entry marker and stop appear on the entry bar." The trade rows from
`execute._trade` do not carry `stop_level`. Fix: include `stop_level` in the trade row and pass it through.

### L10 — low — Request paths used before validation

`backend/app/routes/pine.py:117` calls `_smoke_draft(chosen, python)`, which writes `Path(tmp) / f"{name}.py"`
(`:167`) before `save_user_strategy` checks `name.isidentifier()`. A name such as `../../x` writes outside the
temporary directory. `backend/app/backtest/catalog.py:262` takes the `user:` slug unchecked (the worker re-runs the
AST gate, which limits the damage). Fix: validate `isidentifier()` first, in both places.

### L11 — low — Non-atomic writes

`backend/app/ai/record.py:40` (the AI track record), `backend/app/pine/save.py:57` (the saved strategy),
`backend/app/live/spreads/report.py:118-119`. A crash mid-write leaves a truncated JSON file or module.
Candles, paper days, minute logs, reconcile state, instrument snapshots and option tapes already use temp-then-
`os.replace` (`publish_file`). Fix: use `publish_file` for these too.

### L12 — low — Premium levels off the tick

`backend/app/exits/rules.py:88` rounds to 0.01 (`184.59`). The exchange tick is 0.05. Harmless in simulation, but a
live order at that price would be rejected. Round the stop down and the target up to 0.05 before Phase 7.

### L13 — low — Feed lock and websocket origin

`backend/app/live/connection.py:244-247` releases the flock after every session, so during a backoff another
process can take the feed. The browser websocket `/api/live/ws` does not check `Origin`. It carries only market
data, but any page open in the browser can read it. Fix: hold the lock for the life of the service, and check
`Origin` against `frontend_origin`.

---

## Where the models disagreed

Only one model reviewed this (see the top). These are the judgement calls where a second reviewer could
reasonably take the other side:

- **C2's severity.** For "high": the draft runs in the worker during Convert, before any human sees it. Against:
  the output is a diff you read before Approve, and the attacker must get the model to emit the bypass. I kept it
  high because of the pre-approval execution.
- **H2: block or count?** One view: blocking normal runs over the holdout is too strict for exploratory charts.
  The other: an uncounted peek defeats the holdout. The suggested fix (explicit acknowledgement plus counting) sits
  between the two.
- **L8.** Whether including the cursor minute is look-ahead depends on what the cursor means. The spec wording
  supports the current behaviour.
- **H1** is rated high although no current result is affected, because it is a look-ahead inside the engine's
  indicator path that the existing lookahead test would not catch.

## Verified OK

- **No order code is reachable.** The Upstox client has GET methods only (`upstox/client.py`). The Upstox SDK is
  not a dependency. No order endpoint string or mutating HTTP call to Upstox exists in `backend/app`. Paper only
  records fills. `live_trading` is reported, never acted on.
- **Secrets.** The Upstox token is a `SecretStr`, unwrapped in one place (`config.py:85-87`). OpenAI errors are
  fixed strings (HTTP code or reason only). Authorize errors are redacted. The status route returns a status, not
  the token. The backend binds `127.0.0.1`, and CORS is limited to the frontend origins.
- **Engine timing.** A next-bar-open fill. Live timing puts the market fill at the open of minute end+1, and a stop
  crossed in the lag minute fills at that open, flagged `late`. A strategy's cancel is deferred by the lag.
  `after_square_off` blocks a signal whose fill would land at or after 15:15. A signal on a session's last bar is
  `no_next_bar`. The 15:15 square-off happens at the minute's open, and nothing matches after it. Session-end and
  end-of-data exits happen at the close. Warm-up bars run `on_bar` with trading blocked. Fills inside a bar: market
  orders and gaps first, then a protective stop before anything else; a bracket stop works in the entry bar
  (ambiguous) while the target waits for the next bar; a minute that touches both levels takes the stop. A reversal
  cancels the old bracket. Pyramiding 0 cancels a same-side entry.
- **Context.** `PastBars`, `PastSeries` and `IndicatorView` expose only appended values, and reads past "now" raise
  and are recorded even when swallowed. User strategies always load through `IsolatedStrategy` (the worker re-runs
  `check_source` before `exec_module`).
- **Indicators vs TradingView.** RMA seeded with an SMA. RSI's order of checks (down=0 → 100, then up=0 → 0) and
  first value at bar `length`. ATR = RMA of TR with bar 0 = high − low. Supertrend matches Pine's reference step by
  step, including the `nz()` and `na` quirks. Stochastic = SMA(raw %K), and a flat range is na through the
  smoothing. VWAP resets at IST midnight. Futures VWAP weights by the active contract's 1m volume, with the roll and
  fallback flags. `stdev` is shifted by the first value (no future mean).
- **Option overlay.** ATM is taken from the index fill price. CE for long and PE for short. The lot comes from
  (cycle, expiry), with "new lot only once in force". The expiry calendar uses previous-trading-day holiday shifts.
  The real-premium tape comes first and the model fallback is flagged `modelled`. Delta-adjusted fills are clamped
  to the minute's range. `first_hit` exits at the open on a gap and takes the stop when a minute touches both. The
  option-exceeds-index breach is warned. A trade with more than one fill is refused.
- **Live.** Bars use exchange time only. I1 is final on a later I1 or at session end, and the 15:29 tick bar becomes
  `session_end`. Precedence runs filled < tick < i1 < backfill < official, and every overwrite is diffed. An empty
  official answer changes nothing. Reconcile holds the engine lock only while applying, never across HTTP. There is
  one connection with a flock and `_in_connection` assert, and the URL is re-authorized every time. Paper bars use
  exchange-final minutes only, are emitted in order, and gap bars are flagged late or given up. The paper
  square-off happens on the first frame at or after 15:15:00. A Start after 15:30 writes only to `slotN/replay/`.
  Catch-up decisions are tagged `replay` and left out of the live totals.
- **Data writes.** Candles, paper day files, minute logs, reconcile state, instrument snapshots and option tapes use
  temp-then-`os.replace` and refuse an empty publish. An empty current-session option answer is never marked
  `source_empty`.
- **Frontend replay.** `applyLive` returns false while a cursor is set, which also stops the indicator tail. Candle,
  lazy-load and indicator requests are capped at the cursor, and the backend asserts that no bar is after it.
  Backtest markers are revealed by entry and exit time.
- **Pine approval gates (backend).** Convert requires the exact accepted report hash. Approve requires a draft issued
  by the server with a matching hash and re-checks the AST. Save requires an approved diff hash, and the strategy
  name must be an identifier.

## Not reviewed in depth

Drawing tools and position math (beyond the replay guard), chart memory and performance, the walk-forward
statistics, cost-table values (still unverified by design; test 7d skipped), the AI analyser's validation, and the
calibration fit.
