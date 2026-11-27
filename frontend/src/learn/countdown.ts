/** Header clocks for the Learn tab. Replay does not tick. */

import { allEvents } from "./events";
import { istClock, istEpoch } from "./time";

export function mmss(seconds: number): string {
  const left = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(left / 60);
  const secs = left % 60;
  return `${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
}

export function hhmm(seconds: number): string {
  const left = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(left / 3600);
  const minutes = Math.floor((left % 3600) / 60);
  return `${hours}h ${String(minutes).padStart(2, "0")}m`;
}

export function nextBarClose(now: number, tfSeconds: number): number {
  const clock = istClock(now);
  const [year, month, date] = clock.day.split("-").map(Number);
  const open = istEpoch(year ?? 1970, month ?? 1, date ?? 1, 9, 15);
  const close = istEpoch(year ?? 1970, month ?? 1, date ?? 1, 15, 30);
  if (tfSeconds >= 86400) return now < close ? close : close;
  if (now < open) return open;
  const step = Math.floor((now - open) / tfSeconds) + 1;
  return open + step * tfSeconds;
}

export function nextEvent(now: number, symbol: string): { title: string; at: number } | null {
  for (const item of allEvents()) {
    if (item.symbols.length > 0 && !item.symbols.includes(symbol)) continue;
    const [year, month, date] = item.date.split("-").map(Number);
    const at = istEpoch(year ?? 1970, month ?? 1, date ?? 1, 9, 15);
    if (at > now) return { title: item.verified ? item.title : `${item.title} (unverified)`, at };
  }
  return null;
}

export function countdownLine(now: number, tfLabel: string, tfSeconds: number, replay: boolean, symbol: string): string {
  if (replay) return "Replay — countdowns pause in replay";
  const clock = istClock(now);
  const [year, month, date] = clock.day.split("-").map(Number);
  const close = istEpoch(year ?? 1970, month ?? 1, date ?? 1, 15, 30);
  const weekend = clock.weekday === 0 || clock.weekday === 6;
  const event = nextEvent(now, symbol);
  const eventText = event ? `${event.title} in ${hhmm(event.at - now)}` : "no further event listed";
  if (weekend || now >= close) return `Market closed · ${eventText}`;
  const candle = nextBarClose(now, tfSeconds);
  return `${tfLabel} candle closes in ${mmss(candle - now)} · Market closes in ${hhmm(close - now)} · ${eventText}`;
}
