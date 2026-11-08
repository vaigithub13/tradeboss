import { create } from "zustand";

import { fetchOptionsChain, optionsErrorMessage, type OptionsChainResponse } from "../api/options";

interface OptionsState {
  data: OptionsChainResponse | null;
  expiry: string | null;
  error: string | null;
  loading: boolean;
  setExpiry: (expiry: string) => void;
  load: (signal?: AbortSignal) => Promise<void>;
}

export const useOptionsStore = create<OptionsState>((set, get) => ({
  data: null,
  expiry: null,
  error: null,
  loading: false,
  setExpiry: (expiry) => set({ expiry }),
  load: async (signal) => {
    set({ loading: true });
    try {
      const data = await fetchOptionsChain(signal, get().expiry ?? undefined);
      set({
        data,
        error: null,
        loading: false,
        expiry: get().expiry ?? data.expiry,
      });
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      set({ error: optionsErrorMessage(err), loading: false });
    }
  },
}));
