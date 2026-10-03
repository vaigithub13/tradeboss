import { sanitizeInstances, type IndicatorInstance } from "./catalog";

export const STORAGE_KEY = "chart-analyser.indicators.v1";

type ReadableStorage = Pick<Storage, "getItem">;
type WritableStorage = Pick<Storage, "setItem">;

/** The browser's localStorage, or null where it does not exist / is blocked. */
export function browserStorage(): Storage | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
}

/** Saved indicator settings; anything unreadable or invalid is dropped / repaired, never thrown. */
export function loadItems(storage: ReadableStorage | null = browserStorage()): IndicatorInstance[] {
  if (!storage) return [];
  try {
    const raw = storage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    const items = typeof parsed === "object" && parsed !== null ? (parsed as { items?: unknown }).items : null;
    return sanitizeInstances(items);
  } catch {
    return [];
  }
}

export function saveItems(
  items: readonly IndicatorInstance[],
  storage: WritableStorage | null = browserStorage(),
): void {
  if (!storage) return;
  try {
    storage.setItem(STORAGE_KEY, JSON.stringify({ version: 1, items }));
  } catch {
    // quota / private mode: settings just won't persist
  }
}
