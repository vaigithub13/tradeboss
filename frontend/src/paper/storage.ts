/** localStorage helpers for Paper UI (try/catch — never throw). */

export function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = globalThis.localStorage?.getItem(key);
    if (raw == null) return fallback;
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

export function writeJson(key: string, value: unknown): void {
  try {
    globalThis.localStorage?.setItem(key, JSON.stringify(value));
  } catch {
    // blocked / full — ignore
  }
}

export const PAPER_TAB_KEY = "paper.mainView";
export const PAPER_FILTERS_KEY = "paper.dashboard.filters";
export const PAPER_EQUITY_LEGEND_KEY = "paper.dashboard.equityLegend";
