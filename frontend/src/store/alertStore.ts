import { create } from "zustand";

import { PAPER_SLOTS } from "../api/paper";

/** Paper alert settings. Volume and the per-slot switches are remembered in this browser; whether sound is on is not
 * (a browser allows audio only after a click in each page session). */
export interface AlertSettings {
  volume: number;
  slots: Record<string, boolean>;
}

const KEY = "paper.alerts";

export const DEFAULT_ALERT_SETTINGS: AlertSettings = {
  volume: 0.8,
  slots: Object.fromEntries(PAPER_SLOTS.map((s) => [s, true])),
};

export function readAlertSettings(): AlertSettings {
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return DEFAULT_ALERT_SETTINGS;
    const saved = JSON.parse(raw) as Partial<AlertSettings>;
    const volume = Number(saved.volume);
    return {
      volume: Number.isFinite(volume) ? Math.max(0, Math.min(1, volume)) : DEFAULT_ALERT_SETTINGS.volume,
      slots: { ...DEFAULT_ALERT_SETTINGS.slots, ...(saved.slots ?? {}) },
    };
  } catch {
    return DEFAULT_ALERT_SETTINGS;
  }
}

function writeAlertSettings(s: AlertSettings): void {
  try {
    window.localStorage.setItem(KEY, JSON.stringify({ volume: s.volume, slots: s.slots }));
  } catch {
    // private window or blocked storage: the settings last for this page only
  }
}

interface AlertState extends AlertSettings {
  /** audio unlocked by a click in this page session */
  soundOn: boolean;
  setSoundOn: (on: boolean) => void;
  setVolume: (volume: number) => void;
  setSlot: (slot: string, on: boolean) => void;
}

export const useAlertStore = create<AlertState>((set, get) => ({
  ...readAlertSettings(),
  soundOn: false,
  setSoundOn: (soundOn) => set({ soundOn }),
  setVolume: (volume) => {
    set({ volume: Math.max(0, Math.min(1, volume)) });
    writeAlertSettings(get());
  },
  setSlot: (slot, on) => {
    set({ slots: { ...get().slots, [slot]: on } });
    writeAlertSettings(get());
  },
}));
