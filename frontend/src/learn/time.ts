/** Learn-chart clock. Every label is Asia/Kolkata, matching the main chart. */

import { formatCrosshairTime, formatTick, type TickKind } from "../chart/format";
import type { Timeframe } from "../api/client";

const IST_SHIFT = 5.5 * 60 * 60;

export function istClock(epoch: number): { day: string; minutes: number; weekday: number } {
  const shifted = new Date((epoch + IST_SHIFT) * 1000);
  const y = shifted.getUTCFullYear();
  const m = shifted.getUTCMonth() + 1;
  const d = shifted.getUTCDate();
  return {
    day: `${y}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`,
    minutes: shifted.getUTCHours() * 60 + shifted.getUTCMinutes(),
    weekday: shifted.getUTCDay(),
  };
}

/** Tick-mark type numbers from Lightweight Charts: 0 year, 1 month, 2 day, 3 and 4 time. */
export function learnTickMarkFormatter(time: unknown, tickMarkType: number): string {
  const kind: TickKind = tickMarkType === 0 ? "year" : tickMarkType === 1 ? "month" : tickMarkType === 2 ? "day" : "time";
  return formatTick(Number(time), kind);
}

export function learnTimeFormatter(timeframe: Timeframe): (time: unknown) => string {
  return (time) => formatCrosshairTime(Number(time), timeframe);
}

/** Axis time only, so a session open and close can be checked without a chart. */
export function learnAxisTime(epoch: number): string {
  return formatTick(epoch, "time");
}

export function istEpoch(year: number, month: number, day: number, hour: number, minute: number): number {
  return Math.floor(Date.UTC(year, month - 1, day, hour, minute) / 1000 - IST_SHIFT);
}

export function toIstInput(epoch: number): string {
  const shifted = new Date((epoch + IST_SHIFT) * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${shifted.getUTCFullYear()}-${pad(shifted.getUTCMonth() + 1)}-${pad(shifted.getUTCDate())}T${pad(shifted.getUTCHours())}:${pad(shifted.getUTCMinutes())}`;
}

/** A datetime-local value typed as IST wall time, not the machine zone. */
export function fromIstInput(value: string): number | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(value);
  if (!match) return null;
  return istEpoch(Number(match[1]), Number(match[2]), Number(match[3]), Number(match[4]), Number(match[5]));
}
