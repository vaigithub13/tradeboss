import { useEffect } from "react";

import type { StrategySpec } from "../api/backtests";
import { gridHint, holdoutFormWarning, runLabel, type RunConfig } from "../backtest/present";
import { useBacktestStore } from "../store/backtestStore";
import { useChartStore } from "../store/chartStore";

const STRATEGY_LABEL: Record<string, string> = {
  ema_crossover: "EMA crossover",
  supertrend_flip: "Supertrend flip",
  opening_range_breakout: "Opening range breakout",
  pivot_extension: "Pivot Extension",
  log_xz: "Log XZ",
  price_channel: "Price Channel",
};

export function BacktestPanel() {
  const catalog = useBacktestStore((s) => s.catalog);
  const form = useBacktestStore((s) => s.form);
  const runs = useBacktestStore((s) => s.runs);
  const compareIds = useBacktestStore((s) => s.compareIds);
  const error = useBacktestStore((s) => s.error);
  const busy = useBacktestStore((s) => s.busy);
  const active = useBacktestStore((s) => s.active);
  const loadCatalog = useBacktestStore((s) => s.loadCatalog);
  const setForm = useBacktestStore((s) => s.setForm);
  const setParam = useBacktestStore((s) => s.setParam);
  const start = useBacktestStore((s) => s.start);
  const startWalk = useBacktestStore((s) => s.startWalk);
  const walk = useBacktestStore((s) => s.walk);
  const setWalk = useBacktestStore((s) => s.setWalk);
  const refresh = useBacktestStore((s) => s.refresh);
  const duplicate = useBacktestStore((s) => s.duplicate);
  const openRun = useBacktestStore((s) => s.openRun);
  const toggleCompare = useBacktestStore((s) => s.toggleCompare);
  const setComparing = useBacktestStore((s) => s.setComparing);
  const symbol = useChartStore((s) => s.symbol);
  const timeframe = useChartStore((s) => s.timeframe);
  const sessions = useChartStore((s) => s.sessions);
  const useChartDefaults = useBacktestStore((s) => s.useChartDefaults);

  useEffect(() => {
    void loadCatalog();
    void refresh();
  }, [loadCatalog, refresh]);

  useEffect(() => {
    if (symbol) useChartDefaults(symbol, timeframe, sessions);
  }, [symbol, timeframe, sessions, useChartDefaults]);

  const spec = catalog.find((item) => item.name === form.strategy);
  const holdoutWarning = holdoutFormWarning(form.start, form.end);
  const running = active?.status === "queued" || active?.status === "running" || runs.some((run) => run.status === "queued" || run.status === "running");

  return (
    <aside className="flex h-full w-80 shrink-0 flex-col gap-3 overflow-y-auto border-r border-white/10 bg-[#0b0e14] p-3 text-xs text-white/80">
      <h2 className="text-sm font-semibold text-white">Backtest</h2>
      <label className="flex flex-col gap-1">
        Strategy
        <select
          aria-label="Strategy"
          className="rounded border border-white/10 bg-white/5 px-2 py-1"
          value={form.strategy}
          onChange={(e) => {
            const next = catalog.find((item) => item.name === e.target.value);
            setForm({
              strategy: e.target.value,
              params: next ? defaultsOf(next) : {},
            });
          }}
        >
          {catalog.map((item) => (
            <option key={item.name} value={item.name}>
              {STRATEGY_LABEL[item.name] ?? item.name}
            </option>
          ))}
        </select>
      </label>
      {spec &&
        Object.entries(spec.params).map(([key, field]) => (
          <label key={key} className="flex flex-col gap-1 capitalize">
            {key.replaceAll("_", " ")}
            {field.choices ? (
              <select
                aria-label={key}
                className="rounded border border-white/10 bg-white/5 px-2 py-1"
                value={String(form.params[key] ?? field.default)}
                onChange={(e) => setParam(key, e.target.value)}
              >
                {field.choices.map((choice) => (
                  <option key={choice} value={choice}>
                    {choice}
                  </option>
                ))}
              </select>
            ) : (
              <input
                aria-label={key}
                type="number"
                className="rounded border border-white/10 bg-white/5 px-2 py-1"
                value={Number(form.params[key] ?? field.default)}
                min={field.min}
                step={field.type === "float" ? "0.1" : "1"}
                onChange={(e) => setParam(key, field.type === "float" ? Number(e.target.value) : Number.parseInt(e.target.value, 10))}
              />
            )}
          </label>
        ))}
      <label className="flex flex-col gap-1">
        Symbol
        <input aria-label="Symbol" className="rounded border border-white/10 bg-white/5 px-2 py-1" value={form.symbol} onChange={(e) => setForm({ symbol: e.target.value })} />
      </label>
      <label className="flex flex-col gap-1">
        Timeframe
        <select aria-label="Timeframe" className="rounded border border-white/10 bg-white/5 px-2 py-1" value={form.timeframe} onChange={(e) => setForm({ timeframe: e.target.value })}>
          {["1m", "3m", "5m", "15m", "30m", "1h"].map((tf) => (
            <option key={tf} value={tf}>{tf}</option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1">
        Start
        <input aria-label="Start" type="date" className="rounded border border-white/10 bg-white/5 px-2 py-1" value={form.start} onChange={(e) => setForm({ start: e.target.value })} />
      </label>
      <label className="flex flex-col gap-1">
        End
        <input aria-label="End" type="date" className="rounded border border-white/10 bg-white/5 px-2 py-1" value={form.end ?? ""} onChange={(e) => setForm({ end: e.target.value || null })} />
      </label>
      {holdoutWarning && (
        <p role="status" className="rounded border border-amber-400/40 bg-amber-400/10 px-2 py-1 text-amber-200">
          {holdoutWarning}
        </p>
      )}
      <fieldset className="flex flex-col gap-1">
        <legend>Sessions</legend>
        {(["normal", "weekend_full", "special_short", "muhurat"] as const).map((session) => (
          <label key={session} className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={form.sessions.includes(session)}
              disabled={session === "normal"}
              onChange={() => {
                const has = form.sessions.includes(session);
                const sessions = has ? form.sessions.filter((item) => item !== session) : [...form.sessions, session];
                setForm({ sessions });
              }}
            />
            {session}
          </label>
        ))}
      </fieldset>
      <fieldset className="flex gap-3">
        <legend className="sr-only">Mode</legend>
        <label className="flex items-center gap-1">
          <input type="radio" name="mode" checked={form.mode === "index"} onChange={() => setForm({ mode: "index" })} />
          Index
        </label>
        <label className="flex items-center gap-1">
          <input type="radio" name="mode" checked={form.mode === "options"} onChange={() => setForm({ mode: "options" })} />
          Options
        </label>
      </fieldset>
      <label className="flex flex-col gap-1">
        Strike offset
        <select
          aria-label="Strike offset"
          className="rounded border border-white/10 bg-white/5 px-2 py-1"
          value={form.strike_offset}
          onChange={(e) => setForm({ strike_offset: Number(e.target.value) })}
        >
          <option value={-1}>ATM − 1</option>
          <option value={0}>ATM</option>
          <option value={1}>ATM + 1</option>
        </select>
      </label>
      <label className="flex flex-col gap-1">
        Slippage (points per leg)
        <input
          aria-label="Slippage"
          type="number"
          min={0}
          step={0.5}
          className="rounded border border-white/10 bg-white/5 px-2 py-1"
          value={form.slippage_points}
          onChange={(e) => setForm({ slippage_points: Number(e.target.value) })}
        />
      </label>
      <p className="text-white/40">Square-off 15:15. Fills at the next bar open. Index costs are zero.</p>
      <button
        type="button"
        className="rounded bg-sky-700 px-3 py-1.5 text-white disabled:opacity-40"
        disabled={busy || running}
        onClick={() => void start()}
      >
        Run
      </button>
      <div className="flex flex-col gap-2 rounded border border-white/10 p-2">
        <h3 className="font-semibold text-white">Walk-forward</h3>
        <p className="text-white/40">{gridHint(form.strategy)}. Lots stay 1. Mode stays long/short.</p>
        <div className="grid grid-cols-2 gap-2">
          <NumberField label="Train months" value={walk.train_months} onChange={(value) => setWalk({ train_months: value })} />
          <NumberField label="Test months" value={walk.test_months} onChange={(value) => setWalk({ test_months: value })} />
          <NumberField label="Step months" value={walk.step_months} onChange={(value) => setWalk({ step_months: value })} />
          <NumberField label="Min trades" value={walk.min_trades} onChange={(value) => setWalk({ min_trades: value })} />
          <NumberField label="Max combinations" value={walk.max_combinations} onChange={(value) => setWalk({ max_combinations: value })} />
          <NumberField label="WF slippage" value={walk.slippage_points} step={0.5} onChange={(value) => setWalk({ slippage_points: value })} />
        </div>
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={walk.include_forward}
            onChange={(e) => setWalk({ include_forward: e.target.checked })}
          />
          Include the forward period after 2026-10-01
        </label>
        <button
          type="button"
          className="rounded bg-emerald-800 px-3 py-1.5 text-white disabled:opacity-40"
          disabled={busy || running || form.mode !== "options" || form.symbol !== "NIFTY50"}
          onClick={() => void startWalk()}
        >
          Walk-forward
        </button>
      </div>
      {running && active && (
        <p className="text-white/60">
          {active.progress.phase} {active.progress.done}/{active.progress.total || "…"}
        </p>
      )}
      {error && <p className="text-red-300">{error}</p>}
      <h3 className="mt-2 font-semibold text-white">Saved runs</h3>
      <ul className="flex flex-col gap-2">
        {runs.map((run) => (
          <li key={run.id} className="rounded border border-white/10 p-2">
            <button type="button" className="text-left text-white" onClick={() => void openRun(run.id)}>
              {runLabel(run.config)} · {run.status}
              {run.git_dirty ? " · dirty" : ""}
            </button>
            <div className="mt-1 flex items-center gap-2">
              <button type="button" className="text-sky-300" onClick={() => duplicate(run)}>
                Duplicate with changes
              </button>
              {run.status === "done" && (
                <label className="flex items-center gap-1">
                  <input
                    type="checkbox"
                    aria-label={`Compare ${runLabel(run.config)}`}
                    checked={compareIds.includes(run.id)}
                    onChange={() => toggleCompare(run.id)}
                  />
                  compare
                </label>
              )}
            </div>
          </li>
        ))}
      </ul>
      <button
        type="button"
        className="rounded border border-white/15 px-3 py-1.5 disabled:opacity-40"
        disabled={compareIds.length < 2}
        onClick={() => setComparing(true)}
      >
        Compare
      </button>
    </aside>
  );
}

function NumberField({ label, value, onChange, step = 1 }: { label: string; value: number; onChange: (value: number) => void; step?: number }) {
  return (
    <label className="flex flex-col gap-1">
      {label}
      <input
        aria-label={label}
        type="number"
        min={0}
        step={step}
        className="rounded border border-white/10 bg-white/5 px-2 py-1"
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </label>
  );
}

function defaultsOf(spec: StrategySpec): RunConfig["params"] {
  return Object.fromEntries(Object.entries(spec.params).map(([key, field]) => [key, field.default]));
}
