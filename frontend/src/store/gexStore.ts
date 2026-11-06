import { create } from "zustand";

import { fetchGex, type GexResponse } from "../api/gex";

const KEY = "tradeboss.gex.enabled";

function loadEnabled(): boolean {
  try {
    return globalThis.localStorage?.getItem(KEY) === "1";
  } catch {
    return false;
  }
}

function saveEnabled(on: boolean): void {
  try {
    globalThis.localStorage?.setItem(KEY, on ? "1" : "0");
  } catch {
    // storage can be unavailable; the toggle still works for this tab
  }
}

interface GexState {
  enabled: boolean;
  data: GexResponse | null;
  error: string | null;
  setEnabled: (on: boolean) => void;
  load: (signal?: AbortSignal) => Promise<void>;
}

export const useGexStore = create<GexState>((set) => ({
  enabled: loadEnabled(),
  data: null,
  error: null,
  setEnabled: (on) => {
    saveEnabled(on);
    set(on ? { enabled: true } : { enabled: false, data: null, error: null });
  },
  load: async (signal) => {
    try {
      const data = await fetchGex(signal);
      set({ data, error: null });
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      set({ error: err instanceof Error ? err.message : String(err) });
    }
  },
}));
