import { create } from "zustand";

import { fetchRun, fetchRuns, fetchStrategies, startBacktest, type BacktestRun, type StrategySpec } from "../api/backtests";
import { formFromRun, type RunConfig } from "../backtest/present";
import type { SessionType, Timeframe } from "../api/client";

interface Focus {
  token: number;
  time: number;
}

interface BacktestState {
  panelOpen: boolean;
  catalog: StrategySpec[];
  form: RunConfig;
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
  setParam: (key: string, value: number | string) => void;
  useChartDefaults: (symbol: string, timeframe: Timeframe, sessions: SessionType[]) => void;
  duplicate: (run: BacktestRun) => void;
  refresh: () => Promise<void>;
  start: () => Promise<void>;
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
  end: null,
  sessions: ["normal", "weekend_full"],
  mode: "options",
  strike_offset: 0,
  slippage_points: 0.5,
};

const message = (e: unknown): string => (e instanceof Error ? e.message : String(e));

export const useBacktestStore = create<BacktestState>((set, get) => ({
  panelOpen: false,
  catalog: [],
  form: INITIAL,
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

  setParam: (key, value) => set({ form: { ...get().form, params: { ...get().form.params, [key]: value } } }),

  useChartDefaults: (symbol, timeframe, sessions) => {
    const form = get().form;
    if (form.symbol === symbol && form.timeframe === timeframe) return;
    set({ form: { ...form, symbol, timeframe, sessions: [...sessions] } });
  },

  duplicate: (run) => {
    set({
      panelOpen: true,
      comparing: false,
      form: formFromRun(run.config),
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
      const { id } = await startBacktest(get().form);
      const active = await fetchRun(id);
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
