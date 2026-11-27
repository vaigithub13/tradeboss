/** Learn replay asks for candles the way the main chart does, then hides anything after the cursor. */

import type { CandlesQuery, SessionType, Timeframe } from "../api/client";
import { capCandlesQuery } from "../replay/cap";
import { isHoliday } from "./events";
import { istClock, istEpoch } from "./time";

const OPEN_MINUTE = 9 * 60 + 15;

function addDays(day: string, count: number): string {
  const [year, month, date] = day.split("-").map(Number);
  const next = new Date(Date.UTC(year!, (month ?? 1) - 1, (date ?? 1) + count));
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${next.getUTCFullYear()}-${pad(next.getUTCMonth() + 1)}-${pad(next.getUTCDate())}`;
}

/** 09:15 IST on the last session that has already opened. After the close, that is today. */
export function lastSessionOpen(now: number): number {
  const clock = istClock(now);
  let day = clock.day;
  if (clock.minutes < OPEN_MINUTE) day = addDays(day, -1);
  while (isHoliday(day)) day = addDays(day, -1);
  const [year, month, date] = day.split("-").map(Number);
  return istEpoch(year!, month!, date!, 9, 15);
}

export function learnReplayQuery(
  query: {
    symbol: string;
    timeframe: Timeframe;
    sessions: readonly SessionType[];
    limit: number;
    before?: number;
    after?: number;
  },
  cursor: number,
): CandlesQuery {
  const capped = capCandlesQuery(
    {
      symbol: query.symbol,
      timeframe: query.timeframe,
      sessions: query.sessions,
      limit: query.limit,
      before: query.before,
      after: query.after,
    },
    cursor,
  );
  if (capped.limit != null) {
    const rest = { ...capped };
    delete rest.to;
    delete rest.from;
    return rest;
  }
  return capped;
}

export type ReplayStep =
  | { kind: "move"; cursor: number }
  | { kind: "newer"; after: number }
  | { kind: "older"; before: number };

/** Move the cursor onto the next loaded candle, or ask for the page after the last one. */
export function stepReplay(loaded: readonly { time: number }[], cursor: number): ReplayStep {
  const next = loaded.find((bar) => bar.time > cursor);
  if (next) return { kind: "move", cursor: next.time };
  const shown = loaded.filter((bar) => bar.time <= cursor);
  const last = shown[shown.length - 1] ?? loaded[loaded.length - 1];
  return { kind: "newer", after: last?.time ?? cursor };
}

/** Move the cursor onto the previous loaded candle, or ask for the page before the first one. */
export function backReplay(loaded: readonly { time: number }[], cursor: number): ReplayStep {
  const shown = loaded.filter((bar) => bar.time <= cursor);
  const prev = shown[shown.length - 2];
  if (prev) return { kind: "move", cursor: prev.time };
  const first = shown[0] ?? loaded[0];
  return { kind: "older", before: first?.time ?? cursor + 1 };
}
