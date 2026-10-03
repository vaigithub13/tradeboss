/** The replay cursor shared by candle loads, indicator loads, and the live-bar guard. */

let cursor: number | null = null;

export function replayCursor(): number | null {
  return cursor;
}

export function setReplayCursor(value: number | null): void {
  cursor = value;
}
