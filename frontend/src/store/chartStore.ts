import { create } from "zustand";

import {
  fetchCandles,
  fetchSymbols,
  type Candle,
  type SessionType,
  type SymbolInfo,
  type Timeframe,
} from "../api/client";
import { scopeKey } from "../indicators/cache";
import { mergeLiveCandles } from "../live/merge";
import { acceptBars, capCandlesQuery } from "../replay/cap";
import { niftyReplaySession, sensexReplaySession, type ReplaySession } from "../replay/session";

export type LoadStatus = "idle" | "loading" | "ready" | "error";

const PREFERRED_TIMEFRAME: Timeframe = "15m";
const KNOWN_TIMEFRAMES: readonly string[] = ["1m", "3m", "5m", "15m", "30m", "1h", "1D", "1W"];

/** Where the Sensex tab remembers its timeframe (separate from the Nifty chart). */
export const SENSEX_TIMEFRAME_KEY = "chart-analyser.sensex.tf.v1";

/** A saved timeframe, or null when nothing valid is stored (never throws). */
export function loadSavedTimeframe(key: string): Timeframe | null {
  try {
    const raw = globalThis.localStorage?.getItem(key);
    return raw && KNOWN_TIMEFRAMES.includes(raw) ? (raw as Timeframe) : null;
  } catch {
    return null;
  }
}

export function saveTimeframe(key: string, tf: Timeframe): void {
  try {
    globalThis.localStorage?.setItem(key, tf);
  } catch {
    // private mode / quota: the choice just lasts for this tab
  }
}
const TF_SECONDS: Record<Timeframe, number> = {
  "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "1D": 86400, "1W": 7 * 86400,
};
/** bars fetched when a chart opens (the newest ones) */
export const INITIAL_BARS = 2000;
/** smallest / largest "older" chunk fetched when the user scrolls toward the left edge */
export const OLDER_BARS = 4000;
export const OLDER_BARS_MAX = 20000;

/**
 * Chunk size grows with what is already loaded (min 4k, max 20k): handing data to the chart costs
 * O(bars already loaded) per prepend, so fewer, larger prepends keep the total work down.
 */
export const olderChunkSize = (loaded: number): number => Math.min(OLDER_BARS_MAX, Math.max(OLDER_BARS, loaded));
/**
 * The chart keeps at most this many bars loaded (1m history can be 440k+ bars). When a chunk is
 * added on one side, bars beyond the limit are dropped from the OPPOSITE side (the far-away ones)
 * and fetched again if the user scrolls back: handing data to the chart costs O(bars loaded) per
 * prepend, so a bounded window keeps every prepend fast no matter how long the history is.
 */
export const MAX_WINDOW_BARS = 60_000;
/** how many symbol/timeframe/session scopes keep their loaded candles in memory */
const MAX_CACHED_SCOPES = 10;

/** `older` + `current` joined, keeping at most `max` bars: the far (newest) end is dropped. */
export function prependWindow(older: Candle[], current: Candle[], max = MAX_WINDOW_BARS): { candles: Candle[]; droppedNewer: boolean } {
  const merged = older.length ? [...older, ...current] : current;
  return merged.length > max
    ? { candles: merged.slice(0, max), droppedNewer: true }
    : { candles: merged, droppedNewer: false };
}

/** `current` + `newer` joined, keeping at most `max` bars: the far (oldest) end is dropped. */
export function appendWindow(current: Candle[], newer: Candle[], max = MAX_WINDOW_BARS): { candles: Candle[]; droppedOlder: boolean } {
  const merged = newer.length ? [...current, ...newer] : current;
  return merged.length > max
    ? { candles: merged.slice(merged.length - max), droppedOlder: true }
    : { candles: merged, droppedOlder: false };
}

export interface TimeframeState {
  enabled: boolean;
  /** tooltip text when disabled */
  reason: string | null;
}

/** A timeframe is enabled only if the backend can build it from stored data (never fabricated). */
export function timeframeState(info: SymbolInfo | undefined, tf: Timeframe): TimeframeState {
  if (!info) return { enabled: false, reason: "Loading symbols…" };
  if (info.available_timeframes.includes(tf)) return { enabled: true, reason: null };
  return {
    enabled: false,
    reason: `Needs ${tf} data — stored ${info.display_name} data is ${info.base_timeframe}. Sync its 1m history from Upstox (symbol menu → Sync) to enable it.`,
  };
}

interface Loaded {
  symbol: string;
  timeframe: Timeframe;
  sessions: SessionType[];
}

interface ScopeData {
  candles: Candle[];
  /** older candles exist on the server */
  hasMore: boolean;
  /** newer candles exist on the server (the window dropped them) */
  hasMoreNewer: boolean;
}

/** Loaded chunks per symbol / timeframe / sessions: going back to a timeframe never refetches. */
const scopeCache = new Map<string, ScopeData>();

export function clearChartCache(): void {
  scopeCache.clear();
}

/** Forget cached candles of one symbol (its data changed on the server, e.g. after a sync). */
function clearSymbolCache(symbol: string): void {
  for (const key of [...scopeCache.keys()]) if (key.startsWith(`${symbol}|`)) scopeCache.delete(key);
}

function cacheGet(key: string): ScopeData | undefined {
  const hit = scopeCache.get(key);
  if (hit) {
    scopeCache.delete(key); // refresh LRU position
    scopeCache.set(key, hit);
  }
  return hit;
}

function cachePut(key: string, data: ScopeData): void {
  scopeCache.delete(key);
  scopeCache.set(key, data);
  while (scopeCache.size > MAX_CACHED_SCOPES) {
    const oldest = scopeCache.keys().next().value;
    if (oldest === undefined) break;
    scopeCache.delete(oldest);
  }
}

export const loadedScope = (l: Loaded): string => scopeKey(l.symbol, l.timeframe, l.sessions);

const SESSION_ORDER: readonly SessionType[] = ["normal", "weekend_full", "special_short", "muhurat"];
const sortSessions = (list: readonly SessionType[]): SessionType[] =>
  SESSION_ORDER.filter((t) => list.includes(t));

export interface ChartState {
  symbols: SymbolInfo[];
  symbol: string | null;
  timeframe: Timeframe;
  /** session types included in the chart ('normal' is always on) */
  sessions: SessionType[];
  candles: Candle[];
  /** are there candles older than `candles[0]` on the server? */
  hasMoreOlder: boolean;
  /** are there candles newer than the last loaded one (dropped by the window)? */
  hasMoreNewer: boolean;
  loadingOlder: boolean;
  loadingNewer: boolean;
  /** what `candles` actually contains (differs from the selection while a load is in flight) */
  loaded: Loaded | null;
  status: LoadStatus;
  error: string | null;
  /** Read the symbol list and load the chart; `symbol` overrides the store's default symbol. */
  init: (symbol?: string) => Promise<void>;
  load: () => Promise<void>;
  /** fetch the next older chunk and prepend it (no-op when nothing older / already loading) */
  loadOlder: () => Promise<void>;
  /** fetch the next newer chunk and append it (only after the window dropped newer bars) */
  loadNewer: () => Promise<void>;
  /** switch to another stored symbol (keeps the timeframe when the symbol supports it) */
  setSymbol: (symbol: string) => Promise<void>;
  /** re-read the symbol list (e.g. after a sync); `reload` also drops that symbol's cached candles */
  refreshSymbols: (reload?: string) => Promise<void>;
  setTimeframe: (tf: Timeframe) => Promise<void>;
  toggleSession: (type: SessionType) => Promise<void>;
  /** Replace the loaded window with bars around a unix time (a trade jump). */
  showAround: (time: number) => Promise<void>;
  /** Replace the loaded window with every bar from `from` through `to` (a drawing zoom). */
  showRange: (from: number, to: number) => Promise<void>;
  /**
   * Drop a historical window (showAround / showRange) and reload the newest bars.
   * No-op when the chart already ends at the live tip.
   */
  goLive: () => Promise<void>;
  /**
   * Fold live candles (the newest 1-2 of a timeframe) into the loaded window. Ignored unless that
   * symbol / timeframe is on screen AND the window ends at the newest bar (nothing newer was
   * dropped): a window scrolled back in time must not grow a bar at its edge. Returns whether
   * the candles changed.
   */
  applyLive: (symbol: string, timeframe: string, incoming: readonly Candle[]) => boolean;
  /**
   * Refetch the newest tip bars and merge without duplicate times (stale-feed recovery).
   * No-op while the window is scrolled away from live or replay is on.
   */
  resyncTip: () => Promise<void>;
}

const message = (e: unknown): string => (e instanceof Error ? e.message : String(e));
const isAbort = (e: unknown): boolean => e instanceof DOMException && e.name === "AbortError";

export interface ChartStoreOptions {
  /** the symbol `init()` opens first */
  defaultSymbol: string;
  /** when true, a missing default symbol is an error instead of falling back to the first stored one */
  strictSymbol?: boolean;
  /** the replay cursor this chart honours (default: the Nifty session) */
  session?: ReplaySession;
  /** localStorage key remembering the chosen timeframe (default: not persisted) */
  timeframeStorageKey?: string;
}

/**
 * One chart's loaded window. The Nifty chart and the Sensex tab each get their own store, so the
 * symbol, timeframe, candles and in-flight loads of one never touch the other.
 */
export function createChartStore(opts: ChartStoreOptions) {
  const { defaultSymbol, strictSymbol = false, timeframeStorageKey } = opts;
  const session = opts.session ?? niftyReplaySession;
  const replayCursor = (): number | null => session.get();
  let requestSeq = 0;
  let inflight: AbortController | null = null;

  return create<ChartState>((set, get) => ({
  symbols: [],
  symbol: null,
  timeframe: (timeframeStorageKey ? loadSavedTimeframe(timeframeStorageKey) : null) ?? PREFERRED_TIMEFRAME,
  sessions: ["normal", "weekend_full"],
  candles: [],
  hasMoreOlder: false,
  hasMoreNewer: false,
  loadingOlder: false,
  loadingNewer: false,
  loaded: null,
  status: "idle",
  error: null,

  init: async (symbol) => {
    set({ status: "loading", error: null });
    const target = symbol ?? defaultSymbol;
    try {
      const res = await fetchSymbols();
      const first = res.symbols.find((s) => s.symbol === target) ?? (strictSymbol ? undefined : res.symbols[0]);
      if (!first) {
        set({
          symbols: strictSymbol ? res.symbols : [],
          status: "error",
          error: strictSymbol
            ? `No stored candles for ${target}. Backfill its history first.`
            : "No symbols with stored candles. Run the sample import.",
        });
        return;
      }
      const wanted = (timeframeStorageKey ? loadSavedTimeframe(timeframeStorageKey) : null) ?? PREFERRED_TIMEFRAME;
      const timeframe = first.available_timeframes.includes(wanted)
        ? wanted
        : first.available_timeframes.includes(PREFERRED_TIMEFRAME)
          ? PREFERRED_TIMEFRAME
          : (first.available_timeframes[0] ?? PREFERRED_TIMEFRAME);
      set({
        symbols: res.symbols,
        symbol: first.symbol,
        timeframe,
        sessions: sortSessions(res.default_sessions),
      });
    } catch (e) {
      set({ status: "error", error: `Could not load symbols: ${message(e)}` });
      return;
    }
    await get().load();
  },

  load: async () => {
    const { symbol, timeframe, sessions } = get();
    if (!symbol) return;
    const key = scopeKey(symbol, timeframe, sessions);

    const replayTo = replayCursor();
    const cached = replayTo == null ? cacheGet(key) : undefined;
    if (cached) {
      inflight?.abort();
      requestSeq++;
      set({
        candles: cached.candles,
        hasMoreOlder: cached.hasMore,
        hasMoreNewer: cached.hasMoreNewer,
        loadingOlder: false,
        loadingNewer: false,
        loaded: { symbol, timeframe, sessions },
        status: "ready",
        error: null,
      });
      return;
    }

    inflight?.abort();
    const controller = new AbortController();
    inflight = controller;
    const seq = ++requestSeq;
    set({ status: "loading", error: null });

    try {
      const query = replayTo == null
        ? { symbol, timeframe, sessions, limit: INITIAL_BARS }
        : capCandlesQuery({ symbol, timeframe, sessions, limit: INITIAL_BARS }, replayTo);
      const res = await fetchCandles(query, controller.signal);
      if (seq !== requestSeq) return; // a newer request superseded this one
      const candles = replayTo == null ? res.candles : acceptBars(res.candles, replayTo);
      if (replayTo == null) cachePut(key, { candles, hasMore: res.has_more, hasMoreNewer: false });
      set({
        candles,
        hasMoreOlder: res.has_more,
        hasMoreNewer: false,
        loadingOlder: false,
        loadingNewer: false,
        loaded: { symbol, timeframe, sessions },
        status: "ready",
        error: null,
      });
    } catch (e) {
      if (isAbort(e) || seq !== requestSeq) return;
      set({ status: "error", error: message(e) });
    }
  },

  loadOlder: async () => {
    const { loaded, candles, hasMoreOlder, loadingOlder, loadingNewer, status } = get();
    const first = candles[0];
    if (!loaded || !first || !hasMoreOlder || loadingOlder || loadingNewer || status === "loading") return;
    const key = loadedScope(loaded);
    set({ loadingOlder: true });
    try {
      const replayTo = replayCursor();
      const res = await fetchCandles(
        replayTo == null
          ? {
              symbol: loaded.symbol,
              timeframe: loaded.timeframe,
              sessions: loaded.sessions,
              limit: olderChunkSize(candles.length),
              before: first.time,
            }
          : capCandlesQuery(
              {
                symbol: loaded.symbol,
                timeframe: loaded.timeframe,
                sessions: loaded.sessions,
                limit: olderChunkSize(candles.length),
                before: first.time,
              },
              replayTo,
            ),
      );
      const olderBars = replayTo == null ? res.candles : acceptBars(res.candles, replayTo);
      // The live cache holds whole bars, including the session close. Replay must not merge it back in.
      const live = replayTo == null ? cacheGet(key) : undefined;
      const base = live ?? { candles, hasMore: hasMoreOlder, hasMoreNewer: get().hasMoreNewer };
      const firstNow = base.candles[0]?.time ?? Infinity;
      const older = olderBars.filter((c) => c.time < firstNow);
      const joined = prependWindow(older, base.candles);
      const merged: ScopeData = {
        candles: joined.candles,
        hasMore: res.has_more,
        hasMoreNewer: base.hasMoreNewer || joined.droppedNewer,
      };
      if (replayTo == null) cachePut(key, merged);
      const cur = get().loaded;
      if (cur && loadedScope(cur) === key) {
        set({
          candles: merged.candles,
          hasMoreOlder: merged.hasMore,
          hasMoreNewer: merged.hasMoreNewer,
          loadingOlder: false,
        });
      }
    } catch (e) {
      const cur = get().loaded;
      if (cur && loadedScope(cur) === key) set({ loadingOlder: false, error: message(e) });
    }
  },

  loadNewer: async () => {
    const { loaded, candles, hasMoreNewer, loadingOlder, loadingNewer, status } = get();
    const last = candles[candles.length - 1];
    if (!loaded || !last || !hasMoreNewer || loadingOlder || loadingNewer || status === "loading") return;
    const replayTo = replayCursor();
    if (replayTo != null && last.time >= replayTo) return;
    const key = loadedScope(loaded);
    set({ loadingNewer: true });
    try {
      const res = await fetchCandles(
        replayTo == null
          ? {
              symbol: loaded.symbol,
              timeframe: loaded.timeframe,
              sessions: loaded.sessions,
              limit: olderChunkSize(candles.length),
              after: last.time,
            }
          : capCandlesQuery(
              {
                symbol: loaded.symbol,
                timeframe: loaded.timeframe,
                sessions: loaded.sessions,
                limit: olderChunkSize(candles.length),
                after: last.time,
              },
              replayTo,
            ),
      );
      const newerBars = replayTo == null ? res.candles : acceptBars(res.candles, replayTo);
      const live = replayTo == null ? cacheGet(key) : undefined;
      const base = live ?? { candles, hasMore: get().hasMoreOlder, hasMoreNewer };
      const lastNow = base.candles[base.candles.length - 1]?.time ?? -Infinity;
      const newer = newerBars.filter((c) => c.time > lastNow);
      const joined = appendWindow(base.candles, newer);
      const merged: ScopeData = {
        candles: joined.candles,
        hasMore: base.hasMore || joined.droppedOlder,
        hasMoreNewer: res.has_more_newer,
      };
      if (replayTo == null) cachePut(key, merged);
      const cur = get().loaded;
      if (cur && loadedScope(cur) === key) {
        set({
          candles: merged.candles,
          hasMoreOlder: merged.hasMore,
          hasMoreNewer: merged.hasMoreNewer,
          loadingNewer: false,
        });
      }
    } catch (e) {
      const cur = get().loaded;
      if (cur && loadedScope(cur) === key) set({ loadingNewer: false, error: message(e) });
    }
  },

  setSymbol: async (symbol) => {
    const { symbols, symbol: current, timeframe } = get();
    if (symbol === current) return;
    const info = symbols.find((s) => s.symbol === symbol);
    if (!info) return;
    const next = info.available_timeframes.includes(timeframe)
      ? timeframe
      : info.available_timeframes.includes(PREFERRED_TIMEFRAME)
        ? PREFERRED_TIMEFRAME
        : (info.available_timeframes[0] ?? timeframe);
    set({ symbol, timeframe: next });
    await get().load();
  },

  refreshSymbols: async (reload) => {
    try {
      const res = await fetchSymbols();
      set({ symbols: res.symbols });
    } catch (e) {
      set({ error: `Could not refresh symbols: ${message(e)}` });
      return;
    }
    if (reload) {
      clearSymbolCache(reload);
      const cur = get().symbol;
      if (cur === reload) await get().load();
    }
  },

  setTimeframe: async (tf) => {
    const { symbols, symbol, timeframe } = get();
    if (tf === timeframe) return;
    const info = symbols.find((s) => s.symbol === symbol);
    if (!timeframeState(info, tf).enabled) return;
    set({ timeframe: tf });
    if (timeframeStorageKey) saveTimeframe(timeframeStorageKey, tf);
    await get().load();
  },

  goLive: async () => {
    const { symbol, timeframe, sessions, hasMoreNewer } = get();
    if (!symbol || !hasMoreNewer) return;
    scopeCache.delete(scopeKey(symbol, timeframe, sessions));
    await get().load();
  },

  applyLive: (symbol, timeframe, incoming) => {
    if (replayCursor() != null) return false;
    const { loaded, candles, hasMoreNewer } = get();
    if (!loaded || loaded.symbol !== symbol || loaded.timeframe !== timeframe || hasMoreNewer) return false;
    const merged = mergeLiveCandles(candles, incoming);
    if (!merged.changed) return false;
    const key = loadedScope(loaded);
    const base = cacheGet(key);
    const joined = appendWindow(merged.candles, []); // keep the window bounded
    cachePut(key, { candles: joined.candles, hasMore: (base?.hasMore ?? get().hasMoreOlder) || joined.droppedOlder, hasMoreNewer: false });
    set({ candles: joined.candles, hasMoreOlder: get().hasMoreOlder || joined.droppedOlder });
    return true;
  },

  resyncTip: async () => {
    if (replayCursor() != null) return;
    const { loaded, symbol, timeframe, sessions, hasMoreNewer } = get();
    if (!loaded || !symbol || hasMoreNewer) return;
    try {
      const res = await fetchCandles({ symbol, timeframe, sessions, limit: 4 });
      get().applyLive(symbol, timeframe, res.candles);
    } catch (e) {
      if (!isAbort(e)) console.error("[chart] resyncTip failed", e);
    }
  },

  showRange: async (from, to) => {
    const { symbol, timeframe, sessions } = get();
    if (!symbol) return;
    const start = Math.min(from, to);
    const end = Math.max(from, to);
    try {
      inflight?.abort();
      const seq = ++requestSeq;
      const replayTo = replayCursor();
      const res = await fetchCandles(
        replayTo == null
          ? { symbol, timeframe, sessions, from: start, to: end }
          : capCandlesQuery({ symbol, timeframe, sessions, from: start, to: end }, replayTo),
      );
      if (seq !== requestSeq) return;
      const shown = replayTo == null ? res.candles : acceptBars(res.candles, replayTo);
      const key = scopeKey(symbol, timeframe, sessions);
      if (replayTo == null) cachePut(key, { candles: shown, hasMore: true, hasMoreNewer: true });
      const cur = get();
      if (cur.symbol !== symbol || cur.timeframe !== timeframe) return;
      set({
        candles: shown,
        hasMoreOlder: true,
        hasMoreNewer: replayTo == null,
        loadingOlder: false,
        loadingNewer: false,
        loaded: { symbol, timeframe, sessions },
        status: "ready",
        error: null,
      });
    } catch (e) {
      if (isAbort(e)) return;
      set({ error: message(e) });
    }
  },

  showAround: async (time) => {
    const { symbol, timeframe, sessions } = get();
    if (!symbol) return;
    const step = TF_SECONDS[timeframe];
    try {
      const replayTo = replayCursor();
      const from = time - 80 * step;
      const to = time + 120 * step;
      const res = await fetchCandles(
        replayTo == null
          ? { symbol, timeframe, sessions, from, to }
          : capCandlesQuery({ symbol, timeframe, sessions, from, to }, replayTo),
      );
      const shown = replayTo == null ? res.candles : acceptBars(res.candles, replayTo);
      const key = scopeKey(symbol, timeframe, sessions);
      if (replayTo == null) cachePut(key, { candles: shown, hasMore: true, hasMoreNewer: true });
      set({
        candles: shown,
        hasMoreOlder: true,
        hasMoreNewer: replayTo == null,
        loadingOlder: false,
        loadingNewer: false,
        loaded: { symbol, timeframe, sessions },
        status: "ready",
        error: null,
      });
    } catch (e) {
      set({ error: message(e) });
    }
  },

  toggleSession: async (type) => {
    if (type === "normal") return; // regular sessions are always shown
    const current = get().sessions;
    const next = current.includes(type) ? current.filter((t) => t !== type) : [...current, type];
    set({ sessions: sortSessions(next) });
    await get().load();
  },
  }));
}

export type ChartStoreHook = ReturnType<typeof createChartStore>;

/** The main (Nifty-first) chart. */
export const useChartStore: ChartStoreHook = createChartStore({ defaultSymbol: "NIFTY50" });

/**
 * The Sensex tab's chart: its own symbol / timeframe / candles, its own replay cursor, and its own
 * remembered timeframe. `init(symbol)` is called with the symbol from GET /api/config/charts.
 */
export const useSensexChartStore: ChartStoreHook = createChartStore({
  defaultSymbol: "SENSEX",
  strictSymbol: true,
  session: sensexReplaySession,
  timeframeStorageKey: SENSEX_TIMEFRAME_KEY,
});
