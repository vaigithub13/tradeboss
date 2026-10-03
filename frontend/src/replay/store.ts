import { create } from "zustand";

import type { ResultTrade } from "../api/backtests";
import { useBacktestStore } from "../store/backtestStore";
import { useChartStore } from "../store/chartStore";
import { REPLAY_SPEEDS, nextChartOpen, type ReplaySpeed, type ReplayUnit } from "./cursor";
import { setReplayCursor } from "./session";

const TF_SECONDS = {
  "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "1D": 86400, "1W": 604800,
} as const;

interface ReplayState {
  open: boolean;
  active: boolean;
  playing: boolean;
  cursor: number | null;
  speed: ReplaySpeed;
  unit: ReplayUnit;
  /** Practice mode is not built. The review shortcut stays off while this is true. */
  practice: boolean;
  toggle: () => void;
  start: (cursor: number) => Promise<void>;
  play: () => void;
  pause: () => void;
  step: () => void;
  tick: () => void;
  setSpeed: (speed: number) => void;
  setUnit: (unit: ReplayUnit) => void;
  jumpTrade: () => void;
  exit: () => Promise<void>;
}

function lastStored(): number | null {
  const { symbols, symbol } = useChartStore.getState();
  return symbols.find((item) => item.symbol === symbol)?.last_time ?? null;
}

function advance(cursor: number, unit: ReplayUnit, count: number): number {
  const timeframe = useChartStore.getState().timeframe;
  const step = TF_SECONDS[timeframe];
  let next = cursor;
  for (let i = 0; i < count; i += 1) {
    next = unit === "1m" ? next + 60 : nextChartOpen(next, step);
  }
  const last = lastStored();
  return last == null ? next : Math.min(next, last);
}

async function show(cursor: number): Promise<void> {
  setReplayCursor(cursor);
  await useChartStore.getState().load();
}

export const useReplayStore = create<ReplayState>((set, get) => ({
  open: false,
  active: false,
  playing: false,
  cursor: null,
  speed: 1,
  unit: "chart",
  practice: false,

  toggle: () => set({ open: !get().open }),

  start: async (cursor) => {
    const last = lastStored();
    const snapped = last == null ? cursor : Math.min(cursor, last);
    set({ active: true, playing: false, cursor: snapped, open: true });
    await show(snapped);
  },

  play: () => {
    const { active, cursor } = get();
    if (!active || cursor == null) return;
    set({ playing: true });
  },

  pause: () => set({ playing: false }),

  step: () => {
    const { active, cursor, unit } = get();
    if (!active || cursor == null) return;
    const next = advance(cursor, unit, 1);
    set({ playing: false, cursor: next });
    void show(next);
  },

  tick: () => {
    const { playing, cursor, unit, speed } = get();
    if (!playing || cursor == null) return;
    const next = advance(cursor, unit, speed);
    set({ cursor: next, playing: next !== cursor });
    void show(next);
  },

  setSpeed: (speed) => {
    if (!(REPLAY_SPEEDS as readonly number[]).includes(speed)) return;
    set({ speed: speed as ReplaySpeed });
  },

  setUnit: (unit) => set({ unit }),

  jumpTrade: () => {
    if (get().practice) return;
    const cursor = get().cursor ?? Number.NEGATIVE_INFINITY;
    const trades = useBacktestStore.getState().active?.result?.trades ?? [];
    let next: number | null = null;
    for (const trade of trades) {
      if (trade.entry_time > cursor && (next == null || trade.entry_time < next)) next = trade.entry_time;
    }
    if (next == null) return;
    set({ playing: false, cursor: next, active: true });
    void show(next);
  },

  exit: async () => {
    set({ active: false, playing: false, cursor: null });
    setReplayCursor(null);
    await useChartStore.getState().load();
  },
}));

export function replayTrades(): ResultTrade[] {
  return useBacktestStore.getState().active?.result?.trades ?? [];
}
