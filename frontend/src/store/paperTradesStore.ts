import { create } from "zustand";

import {
  fetchRealTrades,
  saveRealSettings,
  type PaperTradeWithReal,
  type RealTotals,
} from "../api/paper";
import { DEFAULT_ACCOUNT_STOP } from "../paper/real";
import { tradeOverlay, type ColumnKey, type PaperTradeRow, type TradeFilter, type TradeOverlay } from "../paper/trades";
import { useChartStore } from "./chartStore";

export const rowKey = (row: PaperTradeRow): string => `${row.slot}:${row.entry_time}`;

interface PaperTradesState {
  open: boolean;
  full: boolean;
  rows: PaperTradeWithReal[];
  totals: RealTotals | null;
  accountStop: number;
  loading: boolean;
  error: string | null;
  filter: TradeFilter;
  sort: { key: ColumnKey; dir: "asc" | "desc" };
  selected: string | null;
  /** the trade drawn on the chart (not a saved drawing) */
  overlay: TradeOverlay | null;
  focus: { token: number; time: number } | null;
  setOpen: (open: boolean) => void;
  toggleFull: () => void;
  load: () => Promise<void>;
  setFilter: (patch: Partial<TradeFilter>) => void;
  sortBy: (key: ColumnKey) => void;
  select: (row: PaperTradeRow) => Promise<void>;
  clearSelection: () => void;
  setAccountStop: (value: number) => Promise<void>;
}

const emptyTotals = (stop = DEFAULT_ACCOUNT_STOP): RealTotals => ({
  logged: 0,
  day: { date: null, trades: 0, real_net: 0, paper_net: 0, net_diff: 0 },
  running: { trades: 0, real_net: 0, paper_net: 0, net_diff: 0 },
  stop: { account_stop: stop, real_net: 0, loss_used: 0, fraction: 0, reached: false },
});

export const usePaperTradesStore = create<PaperTradesState>((set, get) => ({
  open: false,
  full: false,
  rows: [],
  totals: null,
  accountStop: DEFAULT_ACCOUNT_STOP,
  loading: false,
  error: null,
  filter: { slot: "", from: "", to: "" },
  sort: { key: "entryTime", dir: "desc" },
  selected: null,
  overlay: null,
  focus: null,
  setOpen: (open) => {
    set({ open, full: open ? get().full : false });
    if (open) void get().load();
  },
  toggleFull: () => set({ full: !get().full }),
  load: async () => {
    set({ loading: true });
    try {
      const data = await fetchRealTrades();
      set({
        rows: data.rows,
        totals: data.totals,
        accountStop: data.settings.account_stop,
        error: null,
      });
    } catch (e) {
      set({ error: e instanceof Error ? e.message : "could not load the trades" });
    } finally {
      set({ loading: false });
    }
  },
  setFilter: (patch) => set({ filter: { ...get().filter, ...patch } }),
  sortBy: (key) => {
    const { sort } = get();
    set({ sort: { key, dir: sort.key === key && sort.dir === "desc" ? "asc" : "desc" } });
  },
  select: async (row) => {
    set({ selected: rowKey(row), overlay: tradeOverlay(row), full: false });
    const chart = useChartStore.getState();
    if (chart.symbol !== "NIFTY50") await chart.setSymbol("NIFTY50");
    await useChartStore.getState().showAround(row.entry_time);
    set({ focus: { token: Date.now(), time: row.entry_time } });
  },
  clearSelection: () => set({ selected: null, overlay: null }),
  setAccountStop: async (value) => {
    const settings = await saveRealSettings(value);
    const totals = get().totals ?? emptyTotals(settings.account_stop);
    const stop = {
      ...totals.stop,
      account_stop: settings.account_stop,
      loss_used: Math.max(0, -totals.running.real_net),
      fraction: settings.account_stop
        ? Math.min(1, Math.max(0, -totals.running.real_net) / settings.account_stop)
        : 0,
      reached: Math.max(0, -totals.running.real_net) >= settings.account_stop - 1e-9,
      real_net: totals.running.real_net,
    };
    set({ accountStop: settings.account_stop, totals: { ...totals, stop } });
  },
}));
