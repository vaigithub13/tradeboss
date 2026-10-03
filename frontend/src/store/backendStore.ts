import { create } from "zustand";

import { fetchHealth, type HealthResponse } from "../api/client";

export type BackendStatus = "checking" | "connected" | "disconnected";

interface BackendState {
  status: BackendStatus;
  health: HealthResponse | null;
  poll: (signal?: AbortSignal) => Promise<void>;
}

export const useBackendStore = create<BackendState>((set) => ({
  status: "checking",
  health: null,
  poll: async (signal) => {
    try {
      const health = await fetchHealth(signal);
      set({ status: "connected", health });
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      set({ status: "disconnected", health: null });
    }
  },
}));
