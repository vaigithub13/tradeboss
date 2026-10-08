import { create } from "zustand";

import { fetchRun, fetchRuns, fetchStrategies, startBacktest, startHoldout, startWalkForward, type BacktestRun, type StrategySpec } from "../api/backtests";
import { RESEARCH_END, formFromRun, type RunConfig } from "../backtest/present";
import type { SessionType, Timeframe } from "../api/client";

interface Focus {
  token: number;
  time: number;
}

export interface WalkSettings {
  train_months: number;
  test_months: number;
  step_months: number;
  min_trades: number;
  max_combinations: number;
  include_forward: boolean;
  slippage_points: number;
}

const WALK_INITIAL: WalkSettings = {
  train_months: 6,
  test_months: 2,
  step_months: 2,
  min_trades: 30,
  max_combinations: 50,
  include_forward: false,
  slippage_points: 1,
};

interface BacktestState {
  panelOpen: boolean;
  catalog: StrategySpec[];
  form: RunConfig;
  walk: WalkSettings;
  runs: BacktestRun[];
  active: BacktestRun | null;
  compareIds: string[];
  comparing: boolean;
  selectedTradeId: number | null;
  focus: Focus | null;
  error: string | null;
  busy: boolean;
  setPanelOpen: (open: boolean) => void;
  loadCatalog: () => Promise<void>;
  setForm: (patch: Partial<RunConfig>) => void;
  setWalk: (patch: Partial<WalkSettings>) => void;
  setParam: (key: string, value: number | string | boolean) => void;
  useChartDefaults: (symbol: string, timeframe: Timeframe, sessions: SessionType[]) => void;
  duplicate: (run: BacktestRun) => void;
  refresh: () => Promise<void>;
  start: () => Promise<void>;
  startWalk: () => Promise<void>;
  runHoldout: (id: string) => Promise<void>;
  openRun: (id: string) => Promise<void>;
  toggleCompare: (id: string) => void;
  setComparing: (on: boolean) => void;
  selectTrade: (id: number | null, time: number | null) => void;
}

const INITIAL: RunConfig = {
  strategy: "opening_range_breakout",
  params: { range_minutes: 15, lots: 1 },
  symbol: "NIFTY50",
  timeframe: "5m",
  start: "2024-10-03",
  end: RESEARCH_END,
  sessions: ["normal", "weekend_full"],
  mode: "options",
  strike_offset: 0,
  slippage_points: 0.5,
  live_timing: true,
  exit_rule: null,
};

const message = (e: unknown): string => (e instanceof Error ? e.message : String(e));

export const useBacktestStore = create<BacktestState>((set, get) => ({
  panelOpen: false,
  catalog: [],
  form: INITIAL,
  walk: WALK_INITIAL,
  runs: [],
  active: null,
  compareIds: [],
  comparing: false,
  selectedTradeId: null,
  focus: null,
  error: null,
  busy: false,

  setPanelOpen: (panelOpen) => set({ panelOpen }),

  loadCatalog: async () => {
    try {
      const res = await fetchStrategies();
      set({ catalog: res.strategies });
    } catch (e) {
      set({ error: message(e) });
    }
  },

  setForm: (patch) => set({ form: { ...get().form, ...patch } }),

  setWalk: (patch) => set({ walk: { ...get().walk, ...patch } }),

  setParam: (key, value) => set({ form: { ...get().form, params: { ...get().form.params, [key]: value } } }),

  useChartDefaults: (symbol, timeframe, sessions) => {
    const form = get().form;
    if (form.symbol === symbol && form.timeframe === timeframe) return;
    set({ form: { ...form, symbol, timeframe, sessions: [...sessions] } });
  },

  duplicate: (run) => {
    const form = formFromRun(run.config);
    const walk = run.config.kind === "walk_forward"
      ? {
          train_months: run.config.train_months ?? WALK_INITIAL.train_months,
          test_months: run.config.test_months ?? WALK_INITIAL.test_months,
          step_months: run.config.step_months ?? WALK_INITIAL.step_months,
          min_trades: run.config.min_trades ?? WALK_INITIAL.min_trades,
          max_combinations: run.config.max_combinations ?? WALK_INITIAL.max_combinations,
          include_forward: run.config.include_forward ?? false,
          slippage_points: run.config.slippage_points,
        }
      : get().walk;
    set({
      panelOpen: true,
      comparing: false,
      form,
      walk,
      error: null,
    });
  },

  refresh: async () => {
    try {
      const res = await fetchRuns();
      set({ runs: res.runs });
      const active = get().active;
      if (active && (active.status === "queued" || active.status === "running")) {
        const fresh = await fetchRun(active.id);
        set({ active: fresh });
      }
    } catch (e) {
      set({ error: message(e) });
    }
  },

  start: async () => {
    set({ error: null, busy: true, comparing: false });
    try {
      const { kind: _kind, train_months: _t, test_months: _te, step_months: _s, min_trades: _m, max_combinations: _c, include_forward: _f, ...config } = get().form;
      const { id } = await startBacktest(config);
      const active = await fetchRun(id);
      set({ active, selectedTradeId: null });
      await get().refresh();
    } catch (e) {
      set({ error: message(e) });
    } finally {
      set({ busy: false });
    }
  },

  startWalk: async () => {
    const { form, walk } = get();
    set({ error: null, busy: true, comparing: false });
    try {
      const { id } = await startWalkForward({
        kind: "walk_forward",
        strategy: form.strategy,
        symbol: form.symbol,
        timeframe: form.timeframe,
        start: form.start,
        end: form.end,
        sessions: form.sessions,
        mode: "options",
        strike_offset: form.strike_offset,
        slippage_points: walk.slippage_points,
        live_timing: form.live_timing ?? true,
        exit_rule: form.exit_rule ?? null,
        train_months: walk.train_months,
        test_months: walk.test_months,
        step_months: walk.step_months,
        min_trades: walk.min_trades,
        max_combinations: walk.max_combinations,
        include_forward: walk.include_forward,
      });
      const active = await fetchRun(id);
      set({ active, selectedTradeId: null });
      await get().refresh();
    } catch (e) {
      set({ error: message(e) });
    } finally {
      set({ busy: false });
    }
  },

  runHoldout: async (id) => {
    set({ error: null, busy: true, comparing: false });
    try {
      const started = await startHoldout(id);
      const active = await fetchRun(started.id);
      set({ active, selectedTradeId: null });
      await get().refresh();
    } catch (e) {
      set({ error: message(e) });
    } finally {
      set({ busy: false });
    }
  },

  openRun: async (id) => {
    set({ error: null, comparing: false, selectedTradeId: null });
    try {
      set({ active: await fetchRun(id) });
    } catch (e) {
      set({ error: message(e) });
    }
  },

  toggleCompare: (id) => {
    const current = get().compareIds;
    const next = current.includes(id) ? current.filter((item) => item !== id) : [...current, id];
    set({ compareIds: next.slice(0, 3) });
  },

  setComparing: (comparing) => set({ comparing, selectedTradeId: null }),

  selectTrade: (id, time) => set({
    selectedTradeId: id,
    focus: time === null ? get().focus : { token: Date.now(), time },
  }),
}));
