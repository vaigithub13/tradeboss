import { create } from "zustand";

import { fetchIndicators } from "../api/indicators";
import { applyTail, mergeEntry, scopeKey, trimEntry, type IndicatorEntry } from "../indicators/cache";
import {
  createInstance,
  validateParams,
  type IndicatorInstance,
  type IndicatorType,
  type Params,
} from "../indicators/catalog";
import { loadItems, saveItems } from "../indicators/persistence";
import { buildIndicatorsRequest, planFetches, type FetchGroup, type RequestContext } from "../indicators/request";
import { acceptBars, capIndicatorsRequest } from "../replay/cap";
import { replayCursor } from "../replay/session";

/** scopes (symbol / timeframe / sessions) whose indicator values stay cached; match chartStore */
const MAX_CACHED_SCOPES = 12;

export type IndicatorLoadStatus = "idle" | "loading" | "ready" | "error";

export interface IndicatorPatch {
  params?: Params;
  colors?: Record<string, string>;
  visible?: boolean;
}

/** cached values: scope -> indicator key (type + params) -> one contiguous series */
export type IndicatorData = Record<string, Record<string, IndicatorEntry>>;

interface IndicatorState {
  items: IndicatorInstance[];
  data: IndicatorData;
  status: IndicatorLoadStatus;
  error: string | null;
  add: (type: IndicatorType) => string;
  duplicate: (id: string) => string | null;
  remove: (id: string) => void;
  /** returns an error message when the new parameters are invalid (nothing is changed) */
  update: (id: string, patch: IndicatorPatch) => string | null;
  /**
   * Fetch whatever the loaded candles still lack: only indicators / ranges that are not cached,
   * so colour and visibility edits, timeframe round-trips and unchanged indicators never refetch.
   */
  refresh: (ctx: RequestContext) => Promise<void>;
  /**
   * Overwrite the newest cached values with a live tail (`byKey`: indicator key -> outputs aligned
   * to `times`). Only touches entries that exist and connect to the tail; anything else is left
   * to the normal fetch. Returns whether anything changed.
   */
  applyLiveTail: (scope: string, times: number[], byKey: Record<string, Record<string, (number | null)[]>>) => boolean;
}

const pending = new Set<string>(); // in-flight "scope|key|from-to" jobs (dedupe)
let running = 0;
let scopeOrder: string[] = [];

const message = (e: unknown): string => (e instanceof Error ? e.message : String(e));

/** Forget all cached values (tests). */
export function clearIndicatorCache(): void {
  scopeOrder = [];
  pending.clear();
  running = 0;
  useIndicatorStore.setState({ data: {}, status: "idle", error: null });
}

function touchScope(scope: string, data: IndicatorData): IndicatorData {
  scopeOrder = [...scopeOrder.filter((s) => s !== scope), scope];
  if (scopeOrder.length <= MAX_CACHED_SCOPES) return data;
  const evict = scopeOrder.slice(0, scopeOrder.length - MAX_CACHED_SCOPES);
  scopeOrder = scopeOrder.slice(evict.length);
  const next = { ...data };
  for (const s of evict) delete next[s];
  return next;
}

export const useIndicatorStore = create<IndicatorState>((set, get) => ({
  items: loadItems(),
  data: {},
  status: "idle",
  error: null,

  add: (type) => {
    const inst = createInstance(type, get().items);
    set({ items: [...get().items, inst] });
    return inst.id;
  },

  duplicate: (id) => {
    const src = get().items.find((i) => i.id === id);
    if (!src) return null;
    const copy: IndicatorInstance = {
      ...createInstance(src.type, get().items),
      params: { ...src.params },
    };
    const items = get().items;
    const at = items.findIndex((i) => i.id === id);
    set({ items: [...items.slice(0, at + 1), copy, ...items.slice(at + 1)] });
    return copy.id;
  },

  remove: (id) => set({ items: get().items.filter((i) => i.id !== id) }),

  update: (id, patch) => {
    const current = get().items.find((i) => i.id === id);
    if (!current) return null;
    const params = patch.params ?? current.params;
    const problem = validateParams(current.type, params);
    if (problem) return problem;
    const next: IndicatorInstance = {
      ...current,
      params,
      colors: patch.colors ? { ...current.colors, ...patch.colors } : current.colors,
      visible: patch.visible ?? current.visible,
    };
    set({ items: get().items.map((i) => (i.id === id ? next : i)) });
    return null;
  },

  applyLiveTail: (scope, times, byKey) => {
    const cached = get().data[scope];
    if (!cached) return false;
    let next: Record<string, IndicatorEntry> | null = null;
    for (const [key, outputs] of Object.entries(byKey)) {
      const updated = applyTail(cached[key], { times, outputs });
      if (!updated) continue;
      next = next ?? { ...cached };
      next[key] = updated;
    }
    if (!next) return false;
    set({ data: { ...get().data, [scope]: next } });
    return true;
  },

  refresh: async (ctx) => {
    const scope = scopeKey(ctx.symbol, ctx.timeframe, ctx.sessions);
    const jobs: Promise<void>[] = [];
    trimToWindow(scope, ctx);

    for (const planned of planFetches(ctx, get().data[scope] ?? {})) {
      const jobKey = (key: string): string => `${scope}|${key}|${planned.range.from}-${planned.range.to}`;
      const todo = planned.indicators.filter((i) => !pending.has(jobKey(i.key)));
      if (todo.length === 0) continue; // an identical request is already on its way
      const group: FetchGroup = { range: planned.range, indicators: todo };
      todo.forEach((i) => pending.add(jobKey(i.key)));
      jobs.push(run(scope, ctx, group, jobKey));
    }
    if (jobs.length === 0) return;
    await Promise.all(jobs);
  },
}));

/**
 * Keep cached indicator values bounded together with the chart window: values for bars that are
 * no longer loaded are dropped (and fetched again if the user scrolls back to them).
 */
function trimToWindow(scope: string, ctx: RequestContext): void {
  const first = ctx.candles[0];
  const last = ctx.candles[ctx.candles.length - 1];
  const cached = useIndicatorStore.getState().data[scope];
  if (!first || !last || !cached) return;
  let changed = false;
  const next: Record<string, IndicatorEntry> = {};
  for (const [key, entry] of Object.entries(cached)) {
    const trimmed = trimEntry(entry, first.time, last.time);
    if (trimmed !== entry) changed = true;
    if (trimmed) next[key] = trimmed;
  }
  if (changed) useIndicatorStore.setState({ data: { ...useIndicatorStore.getState().data, [scope]: next } });
}

async function run(
  scope: string,
  ctx: RequestContext,
  group: FetchGroup,
  jobKey: (key: string) => string,
): Promise<void> {
  const { set, get } = { set: useIndicatorStore.setState, get: useIndicatorStore.getState };
  running++;
  set({ status: "loading", error: null });
  try {
    const cursor = replayCursor();
    const request = cursor == null ? buildIndicatorsRequest(ctx, group) : capIndicatorsRequest(buildIndicatorsRequest(ctx, group), cursor);
    const res = await fetchIndicators(request);
    if (cursor != null) acceptBars(res.times.map((time) => ({ time })), cursor);
    const forScope = { ...(get().data[scope] ?? {}) };
    for (const out of res.indicators) {
      const planned = group.indicators[Number(out.id)];
      if (!planned) continue;
      forScope[planned.key] = mergeEntry(forScope[planned.key], { times: res.times, outputs: out.outputs });
    }
    set({ data: touchScope(scope, { ...get().data, [scope]: forScope }) });
  } catch (e) {
    set({ status: "error", error: message(e) });
  } finally {
    running--;
    group.indicators.forEach((i) => pending.delete(jobKey(i.key)));
    if (running === 0 && useIndicatorStore.getState().status === "loading") set({ status: "ready" });
  }
}

// Persist settings (not results) whenever the item list changes.
useIndicatorStore.subscribe((state, prev) => {
  if (state.items !== prev.items) saveItems(state.items);
});
