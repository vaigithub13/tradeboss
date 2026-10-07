import { create } from "zustand";

import { fetchPaperStatus, startPaper, stopPaper, type PaperDeskStatus } from "../api/paper";

interface PaperState {
  status: PaperDeskStatus | null;
  /** the last error, per slot ("" for the status poll) */
  errors: Record<string, string | null>;
  refresh: () => Promise<void>;
  start: (slot: string, strategy: string, params: Record<string, unknown>) => Promise<void>;
  stop: (slot: string) => Promise<void>;
}

const messageOf = (e: unknown): string => (e instanceof Error ? e.message : "request failed");

export const usePaperStore = create<PaperState>((set, get) => ({
  status: null,
  errors: {},
  refresh: async () => {
    try {
      set({ status: await fetchPaperStatus(), errors: { ...get().errors, "": null } });
    } catch (e) {
      set({ errors: { ...get().errors, "": messageOf(e) } });
    }
  },
  start: async (slot, strategy, params) => {
    try {
      await startPaper(slot, strategy, params);
      set({ errors: { ...get().errors, [slot]: null } });
    } catch (e) {
      set({ errors: { ...get().errors, [slot]: messageOf(e) } });
    }
    await get().refresh();
  },
  stop: async (slot) => {
    try {
      await stopPaper(slot);
      set({ errors: { ...get().errors, [slot]: null } });
    } catch (e) {
      set({ errors: { ...get().errors, [slot]: messageOf(e) } });
    }
    await get().refresh();
  },
}));
