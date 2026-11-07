import { create } from "zustand";

import { fetchMarketView, fetchMarketViewHistory, type MarketView, type MarketViewHistory } from "../api/marketView";
import { istDate, shouldOpenPanel } from "../marketView/present";

const COLLAPSED_KEY = "tradeboss.marketView.collapsedOn";
const LEVELS_KEY = "tradeboss.marketView.levels";

function read(key: string): string | null {
  try {
    return globalThis.localStorage?.getItem(key) ?? null;
  } catch {
    return null;
  }
}

function write(key: string, value: string): void {
  try {
    globalThis.localStorage?.setItem(key, value);
  } catch {
    // storage unavailable: the setting lasts for this tab only
  }
}

const nowSeconds = () => Date.now() / 1000;

interface MarketViewState {
  view: MarketView | null;
  history: MarketViewHistory | null;
  error: string | null;
  loading: boolean;
  panelOpen: boolean;
  showLevels: boolean;
  setPanelOpen: (open: boolean) => void;
  setShowLevels: (on: boolean) => void;
  load: (refresh?: boolean, signal?: AbortSignal) => Promise<void>;
}

export const useMarketViewStore = create<MarketViewState>((set) => ({
  view: null,
  history: null,
  error: null,
  loading: false,
  panelOpen: shouldOpenPanel(nowSeconds(), read(COLLAPSED_KEY)),
  showLevels: read(LEVELS_KEY) !== "0",
  setPanelOpen: (open) => {
    if (!open) write(COLLAPSED_KEY, istDate(nowSeconds()));
    set({ panelOpen: open });
  },
  setShowLevels: (on) => {
    write(LEVELS_KEY, on ? "1" : "0");
    set({ showLevels: on });
  },
  load: async (refresh = false, signal) => {
    set({ loading: true });
    try {
      const [view, history] = await Promise.all([
        fetchMarketView(refresh, signal),
        fetchMarketViewHistory(signal).catch(() => null),
      ]);
      set({ view, history, error: null, loading: false });
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      set({ error: err instanceof Error ? err.message : String(err), loading: false });
    }
  },
}));
