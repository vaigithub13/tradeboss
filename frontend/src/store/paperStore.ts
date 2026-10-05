import { create } from "zustand";

import { fetchPaperStatus, startPaper, stopPaper, type PaperStatus } from "../api/paper";

interface PaperState {
  status: PaperStatus | null;
  error: string | null;
  refresh: () => Promise<void>;
  start: (strategy: string) => Promise<void>;
  stop: () => Promise<void>;
}

const messageOf = (e: unknown): string => (e instanceof Error ? e.message : "request failed");

export const usePaperStore = create<PaperState>((set) => ({
  status: null,
  error: null,
  refresh: async () => {
    try {
      set({ status: await fetchPaperStatus(), error: null });
    } catch (e) {
      set({ error: messageOf(e) });
    }
  },
  start: async (strategy) => {
    try {
      set({ status: await startPaper(strategy), error: null });
    } catch (e) {
      set({ error: messageOf(e) });
    }
  },
  stop: async () => {
    try {
      set({ status: await stopPaper(), error: null });
    } catch (e) {
      set({ error: messageOf(e) });
    }
  },
}));
