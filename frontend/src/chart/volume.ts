import type { Candle } from "../api/client";

/** Volume pane is shown only if at least one candle in the loaded range has volume > 0. */
export function hasVolume(candles: readonly Candle[]): boolean {
  for (const c of candles) {
    if (c.volume > 0) return true;
  }
  return false;
}
