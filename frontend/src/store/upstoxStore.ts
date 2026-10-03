import { create } from "zustand";

import {
  fetchCoverage,
  fetchSyncJob,
  fetchTokenStatus,
  startSync,
  type InstrumentHit,
  type SyncJob,
  type TokenStatus,
} from "../api/client";
import { useChartStore } from "./chartStore";

/** how often a running sync job is polled */
export const JOB_POLL_MS = 1000;
/** "Load more history" goes back this many days before the oldest 1m data that is stored */
export const LOAD_MORE_DAYS = 90;

const message = (e: unknown): string => (e instanceof Error ? e.message : String(e));
const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));

/** YYYY-MM-DD, `days` before the given YYYY-MM-DD (UTC arithmetic: only dates, no clock). */
export function daysBefore(date: string, days: number): string {
  const d = new Date(`${date}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() - days);
  return d.toISOString().slice(0, 10);
}

interface UpstoxState {
  token: TokenStatus | null;
  /** the status request itself failed (backend down) */
  tokenCheckFailed: boolean;
  /** latest sync job per instrument key */
  jobs: Record<string, SyncJob>;
  pollToken: (refresh?: boolean) => Promise<void>;
  /** start a sync and follow it to the end; resolves with the final job (null if it could not start) */
  sync: (instrumentKey: string, fromDate?: string) => Promise<SyncJob | null>;
  /** open an instrument in the chart: stored -> switch; otherwise fetch its 1m history first */
  openInstrument: (hit: Pick<InstrumentHit, "instrument_key" | "symbol_id" | "has_data">) => Promise<void>;
  /** fetch another LOAD_MORE_DAYS of older 1m history for an instrument */
  loadMoreHistory: (instrumentKey: string) => Promise<void>;
  /** bring an instrument's 1m data up to date */
  syncLatest: (instrumentKey: string) => Promise<void>;
}

export const useUpstoxStore = create<UpstoxState>((set, get) => ({
  token: null,
  tokenCheckFailed: false,
  jobs: {},

  pollToken: async (refresh = false) => {
    try {
      const token = await fetchTokenStatus(refresh);
      set({ token, tokenCheckFailed: false });
    } catch {
      set({ tokenCheckFailed: true });
    }
  },

  sync: async (instrumentKey, fromDate) => {
    const put = (job: SyncJob): void => set({ jobs: { ...get().jobs, [instrumentKey]: job } });
    let job: SyncJob;
    try {
      job = await startSync(instrumentKey, fromDate);
    } catch (e) {
      set({ jobs: { ...get().jobs, [instrumentKey]: failedJob(instrumentKey, message(e)) } });
      return null;
    }
    put(job);
    while (job.status === "running") {
      await sleep(JOB_POLL_MS);
      try {
        job = await fetchSyncJob(job.id);
      } catch (e) {
        job = { ...job, status: "error", error: message(e), message: message(e) };
      }
      put(job);
    }
    if (job.auth_error) void get().pollToken(true); // the header badge must say "invalid / missing"
    return job;
  },

  openInstrument: async (hit) => {
    const chart = useChartStore.getState();
    if (hit.has_data) {
      await chart.refreshSymbols();
      await useChartStore.getState().setSymbol(hit.symbol_id);
      return;
    }
    const job = await get().sync(hit.instrument_key);
    if (job?.status !== "done") return; // the error stays visible on the job
    await useChartStore.getState().refreshSymbols(job.symbol);
    await useChartStore.getState().setSymbol(job.symbol);
  },

  loadMoreHistory: async (instrumentKey) => {
    let from: string | undefined;
    try {
      const cov = await fetchCoverage(instrumentKey);
      const oldest = cov.covered[0]?.[0];
      if (oldest) {
        from = daysBefore(oldest, LOAD_MORE_DAYS);
        if (from < cov.min_history_date) from = cov.min_history_date;
      }
    } catch (e) {
      set({ jobs: { ...get().jobs, [instrumentKey]: failedJob(instrumentKey, message(e)) } });
      return;
    }
    const job = await get().sync(instrumentKey, from);
    if (job?.status === "done") await useChartStore.getState().refreshSymbols(job.symbol);
  },

  syncLatest: async (instrumentKey) => {
    const job = await get().sync(instrumentKey);
    if (job?.status === "done") await useChartStore.getState().refreshSymbols(job.symbol);
  },
}));

function failedJob(instrumentKey: string, error: string): SyncJob {
  return {
    id: "",
    instrument_key: instrumentKey,
    symbol: "",
    status: "error",
    windows_total: 0,
    windows_done: 0,
    bars_added: 0,
    message: error,
    error,
    auth_error: false,
    started_at: "",
    finished_at: null,
  };
}
