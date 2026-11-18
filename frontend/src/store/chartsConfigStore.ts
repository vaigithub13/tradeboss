import { create } from "zustand";

import { FALLBACK_CHARTS_CONFIG, fetchChartsConfig, type ChartsConfig } from "../api/chartsConfig";

interface ChartsConfigState {
  config: ChartsConfig | null;
  /** "fallback" = the endpoint failed and the built-in folder names are in use */
  source: "none" | "backend" | "fallback";
  /** Fetch once; later calls return the same result. A failed fetch falls back, and the next call retries. */
  load: () => Promise<ChartsConfig>;
}

let inflight: Promise<ChartsConfig> | null = null;

export const useChartsConfigStore = create<ChartsConfigState>((set, get) => ({
  config: null,
  source: "none",
  load: () => {
    const have = get().config;
    if (have && get().source === "backend") return Promise.resolve(have);
    if (!inflight) {
      inflight = fetchChartsConfig()
        .then((config) => {
          set({ config, source: "backend" });
          return config;
        })
        .catch((err: unknown) => {
          console.error("[config] /api/config/charts failed, using fallback names", err);
          set({ config: FALLBACK_CHARTS_CONFIG, source: "fallback" });
          inflight = null; // let the next caller retry
          return FALLBACK_CHARTS_CONFIG;
        });
    }
    return inflight;
  },
}));

/** Reset the fetch-once memo (tests). */
export function resetChartsConfig(): void {
  inflight = null;
  useChartsConfigStore.setState({ config: null, source: "none" });
}
