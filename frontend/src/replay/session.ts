/**
 * The replay cursor shared by candle loads, indicator loads, and the live-bar guard.
 *
 * Each chart (Nifty, Sensex) owns one session so replaying one never freezes the other's live feed.
 * The module-level `replayCursor` / `setReplayCursor` are the Nifty session (the original API).
 */

export interface ReplaySession {
  get: () => number | null;
  set: (value: number | null) => void;
}

export function createReplaySession(): ReplaySession {
  let cursor: number | null = null;
  return {
    get: () => cursor,
    set: (value) => {
      cursor = value;
    },
  };
}

export const niftyReplaySession: ReplaySession = createReplaySession();
export const sensexReplaySession: ReplaySession = createReplaySession();

export function replayCursor(): number | null {
  return niftyReplaySession.get();
}

export function setReplayCursor(value: number | null): void {
  niftyReplaySession.set(value);
}
