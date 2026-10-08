/** Panel sizes the user dragged, remembered in this browser (localStorage; a blocked or empty store falls back). */

export function clampSize(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, Math.round(value)));
}

export function readSize(key: string, fallback: number, min: number, max: number): number {
  try {
    const raw = window.localStorage.getItem(key);
    const value = raw == null ? NaN : Number(raw);
    return Number.isFinite(value) ? clampSize(value, min, max) : fallback;
  } catch {
    return fallback;
  }
}

export function writeSize(key: string, value: number): void {
  try {
    window.localStorage.setItem(key, String(Math.round(value)));
  } catch {
    // private window or blocked storage: the size is kept for this page only
  }
}

/** The new size while dragging an edge: `start` size, pointer moved `delta` px, the edge grows toward `sign`. */
export function dragSize(start: number, delta: number, sign: 1 | -1, min: number, max: number): number {
  return clampSize(start + sign * delta, min, max);
}
