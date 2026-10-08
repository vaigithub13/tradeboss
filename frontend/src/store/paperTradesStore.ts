import { create } from "zustand";

import { fetchPaperTrades } from "../api/paper";
import { tradeOverlay, type ColumnKey, type PaperTradeRow, type TradeFilter, type TradeOverlay } from "../paper/trades";
import { useChartStore } from "./chartStore";

export const rowKey = (row: PaperTradeRow): string => `${row.slot}:${row.entry_time}`;

interface PaperTradesState {
  open: boolean;
  full: boolean;
  rows: PaperTradeRow[];
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
}

export const usePaperTradesStore = create<PaperTradesState>((set, get) => ({
  open: false,
  full: false,
  rows: [],
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
      const { rows } = await fetchPaperTrades();
      set({ rows, error: null });
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
}));
