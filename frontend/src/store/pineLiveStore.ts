/**
 * Live Pine scripts + Alerts log. Client-only — never folded into liveView / WS.
 */
import { create } from "zustand";

import type { ChartScopeId } from "../pine/closedBars";
import type { PineSide, PineSignal } from "../pine/signals";

const LOG_KEY = "pine.alerts.log";
const SCRIPTS_KEY = "pine.live.scripts";
const MAX_SCRIPTS = 5;
const MAX_LOG = 500;

export interface LivePineScript {
  id: string;
  name: string;
  source: string;
  scope: ChartScopeId;
  symbol: string;
  timeframe: string;
  enabled: boolean;
  muted: boolean;
  error: string | null;
  notices: string[];
  repaintWarnings: string[];
}

export interface PineAlertRow {
  id: string;
  time: number;
  scriptId: string;
  scriptName: string;
  symbol: string;
  timeframe: string;
  side: PineSide;
  text: string;
  price: number;
  day: string; // YYYY-MM-DD IST
}

export interface PineMarkerSignal extends PineSignal {
  scriptId: string;
  label: string;
}

interface PersistedScript {
  id: string;
  name: string;
  source: string;
  scope: ChartScopeId;
  symbol: string;
  timeframe: string;
  enabled: boolean;
  muted: boolean;
}

function istDay(unixSec: number): string {
  const d = new Date((unixSec + 19800) * 1000);
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, "0");
  const day = String(d.getUTCDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function todayIst(): string {
  return istDay(Math.floor(Date.now() / 1000));
}

function loadScripts(): LivePineScript[] {
  try {
    const raw = globalThis.localStorage?.getItem(SCRIPTS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as PersistedScript[];
    return parsed.slice(0, MAX_SCRIPTS).map((s) => ({
      ...s,
      error: null,
      notices: [],
      repaintWarnings: [],
    }));
  } catch {
    return [];
  }
}

function saveScripts(scripts: LivePineScript[]): void {
  try {
    const slim: PersistedScript[] = scripts.map(({ id, name, source, scope, symbol, timeframe, enabled, muted }) => ({
      id, name, source, scope, symbol, timeframe, enabled, muted,
    }));
    globalThis.localStorage?.setItem(SCRIPTS_KEY, JSON.stringify(slim));
  } catch {
    // ignore
  }
}

function loadLog(): PineAlertRow[] {
  try {
    const raw = globalThis.localStorage?.getItem(LOG_KEY);
    if (!raw) return [];
    const rows = JSON.parse(raw) as PineAlertRow[];
    const today = todayIst();
    return rows.filter((r) => r.day === today).slice(0, MAX_LOG);
  } catch {
    return [];
  }
}

function saveLog(rows: PineAlertRow[]): void {
  try {
    const today = todayIst();
    const kept = rows.filter((r) => r.day === today).slice(0, MAX_LOG);
    globalThis.localStorage?.setItem(LOG_KEY, JSON.stringify(kept));
  } catch {
    // ignore
  }
}

interface PineLiveState {
  scripts: LivePineScript[];
  /** Fired signals kept for chart markers (per script). */
  markersByScript: Record<string, PineMarkerSignal[]>;
  log: PineAlertRow[];
  logFilterScriptId: string | null;
  replaySounds: boolean;
  soundOn: boolean;
  addScript: (input: {
    name: string;
    source: string;
    scope: ChartScopeId;
    symbol: string;
    timeframe: string;
    repaintWarnings?: string[];
    notices?: string[];
  }) => { ok: true; id: string } | { ok: false; error: string };
  setEnabled: (id: string, enabled: boolean) => void;
  setMuted: (id: string, muted: boolean) => void;
  setError: (id: string, error: string | null) => void;
  setNotices: (id: string, notices: string[]) => void;
  removeScript: (id: string) => void;
  replaceMarkers: (scriptId: string, signals: PineMarkerSignal[]) => void;
  appendMarkers: (scriptId: string, signals: PineMarkerSignal[]) => void;
  appendAlerts: (rows: PineAlertRow[]) => void;
  clearLog: () => void;
  setLogFilter: (scriptId: string | null) => void;
  setReplaySounds: (on: boolean) => void;
  setSoundOn: (on: boolean) => void;
  markersForScope: (scope: ChartScopeId) => PineMarkerSignal[];
}

let seq = 0;
function newId(): string {
  seq += 1;
  return `pine-${Date.now().toString(36)}-${seq}`;
}

export const usePineLiveStore = create<PineLiveState>((set, get) => ({
  scripts: loadScripts(),
  markersByScript: {},
  log: loadLog(),
  logFilterScriptId: null,
  replaySounds: false,
  soundOn: false,

  addScript: (input) => {
    const cur = get().scripts;
    if (cur.length >= MAX_SCRIPTS) return { ok: false, error: `Max ${MAX_SCRIPTS} live scripts` };
    const id = newId();
    const next: LivePineScript = {
      id,
      name: input.name,
      source: input.source,
      scope: input.scope,
      symbol: input.symbol,
      timeframe: input.timeframe,
      enabled: true,
      muted: false,
      error: null,
      notices: input.notices ?? [],
      repaintWarnings: input.repaintWarnings ?? [],
    };
    const scripts = [...cur, next];
    saveScripts(scripts);
    set({ scripts });
    return { ok: true, id };
  },

  setEnabled: (id, enabled) => {
    const scripts = get().scripts.map((s) => (s.id === id ? { ...s, enabled } : s));
    saveScripts(scripts);
    set({ scripts });
  },

  setMuted: (id, muted) => {
    const scripts = get().scripts.map((s) => (s.id === id ? { ...s, muted } : s));
    saveScripts(scripts);
    set({ scripts });
  },

  setError: (id, error) => {
    const scripts = get().scripts.map((s) =>
      s.id === id ? { ...s, error, enabled: error ? false : s.enabled } : s,
    );
    saveScripts(scripts);
    set({ scripts });
  },

  setNotices: (id, notices) => {
    const scripts = get().scripts.map((s) => (s.id === id ? { ...s, notices } : s));
    set({ scripts });
  },

  removeScript: (id) => {
    const scripts = get().scripts.filter((s) => s.id !== id);
    const markersByScript = { ...get().markersByScript };
    delete markersByScript[id];
    saveScripts(scripts);
    set({ scripts, markersByScript });
  },

  replaceMarkers: (scriptId, signals) => {
    set({ markersByScript: { ...get().markersByScript, [scriptId]: signals } });
  },

  appendMarkers: (scriptId, signals) => {
    const prev = get().markersByScript[scriptId] ?? [];
    const keys = new Set(prev.map((s) => `${s.time}|${s.side}|${s.text}`));
    const added = signals.filter((s) => !keys.has(`${s.time}|${s.side}|${s.text}`));
    if (added.length === 0) return;
    set({ markersByScript: { ...get().markersByScript, [scriptId]: [...prev, ...added] } });
  },

  appendAlerts: (rows) => {
    if (rows.length === 0) return;
    const log = [...rows, ...get().log].slice(0, MAX_LOG);
    saveLog(log);
    set({ log });
  },

  clearLog: () => {
    saveLog([]);
    set({ log: [] });
  },

  setLogFilter: (scriptId) => set({ logFilterScriptId: scriptId }),

  setReplaySounds: (on) => set({ replaySounds: on }),

  setSoundOn: (on) => set({ soundOn: on }),

  markersForScope: (scope) => {
    const { scripts, markersByScript } = get();
    const out: PineMarkerSignal[] = [];
    for (const s of scripts) {
      if (!s.enabled || s.scope !== scope) continue;
      out.push(...(markersByScript[s.id] ?? []));
    }
    return out;
  },
}));

export function pineAlertRow(
  script: Pick<LivePineScript, "id" | "name" | "symbol" | "timeframe">,
  signal: PineSignal,
): PineAlertRow {
  return {
    id: `${script.id}-${signal.time}-${signal.side}-${signal.text}`,
    time: signal.time,
    scriptId: script.id,
    scriptName: script.name,
    symbol: script.symbol,
    timeframe: script.timeframe,
    side: signal.side,
    text: signal.text,
    price: signal.price,
    day: istDay(signal.time),
  };
}

export { MAX_SCRIPTS, todayIst, istDay };
