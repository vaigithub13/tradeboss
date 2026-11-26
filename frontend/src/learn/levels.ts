/**
 * Levels from the candles already on the Learn chart. Same CPR as the market view:
 * pivot = (H + L + C) / 3, BC = (H + L) / 2, TC = 2 × pivot − BC, then top = max(TC, BC).
 * Previous day and previous ISO week are in IST. The opening range is 09:15–09:45 IST
 * (a bar whose start is in that window) and is drawn once a later bar on that day has closed.
 */

import type { LearnBar } from "./structure";

const IST_SHIFT = 5.5 * 60 * 60;

export interface DayParts {
  day: string;
  week: string;
  minutes: number;
}

export interface Cpr {
  pdh: number;
  pdl: number;
  prevClose: number;
  pivot: number;
  tc: number;
  bc: number;
}

export interface LevelLine {
  id: string;
  label: string;
  price: number;
  layer: "pdh" | "pdl" | "prevClose" | "week" | "cpr" | "round" | "opening";
}

export function istParts(epoch: number): DayParts {
  const shifted = new Date((epoch + IST_SHIFT) * 1000);
  const y = shifted.getUTCFullYear();
  const m = shifted.getUTCMonth() + 1;
  const d = shifted.getUTCDate();
  const day = `${y}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
  const minutes = shifted.getUTCHours() * 60 + shifted.getUTCMinutes();
  const utc = new Date(Date.UTC(y, m - 1, d));
  const weekday = utc.getUTCDay() || 7;
  utc.setUTCDate(utc.getUTCDate() + 4 - weekday);
  const yearStart = new Date(Date.UTC(utc.getUTCFullYear(), 0, 1));
  const weekNo = Math.ceil(((utc.getTime() - yearStart.getTime()) / 86400000 + 1) / 7);
  return { day, week: `${utc.getUTCFullYear()}-W${String(weekNo).padStart(2, "0")}`, minutes };
}

export function cprFrom(high: number, low: number, close: number): Cpr {
  const pivot = (high + low + close) / 3;
  const bc = (high + low) / 2;
  const tc = 2 * pivot - bc;
  return {
    pdh: high,
    pdl: low,
    prevClose: close,
    pivot,
    tc: Math.max(tc, bc),
    bc: Math.min(tc, bc),
  };
}

export function roundLevels(low: number, high: number, last: number, step = 100, cap = 40): number[] {
  if (!(high >= low) || step <= 0) return [];
  const start = Math.ceil(low / step) * step;
  const end = Math.floor(high / step) * step;
  const all: number[] = [];
  for (let price = start; price <= end + 1e-9; price += step) all.push(Math.round(price));
  if (all.length <= cap) return all;
  return all.sort((a, b) => Math.abs(a - last) - Math.abs(b - last)).slice(0, cap).sort((a, b) => a - b);
}

function dayStats(bars: readonly LearnBar[], day: string): { high: number; low: number; close: number } | null {
  const rows = bars.filter((bar) => istParts(bar.time).day === day);
  if (rows.length === 0) return null;
  return {
    high: Math.max(...rows.map((bar) => bar.high)),
    low: Math.min(...rows.map((bar) => bar.low)),
    close: rows[rows.length - 1]!.close,
  };
}

export function readLevels(bars: readonly LearnBar[]): LevelLine[] {
  if (bars.length === 0) return [];
  const last = bars[bars.length - 1]!;
  const today = istParts(last.time);
  const days = [...new Set(bars.map((bar) => istParts(bar.time).day))].sort();
  const prevDay = [...days].reverse().find((day) => day < today.day) ?? null;
  const lines: LevelLine[] = [];
  if (prevDay) {
    const stats = dayStats(bars, prevDay);
    if (stats) {
      const cpr = cprFrom(stats.high, stats.low, stats.close);
      lines.push(
        { id: "pdh", label: "PDH", price: round2(cpr.pdh), layer: "pdh" },
        { id: "pdl", label: "PDL", price: round2(cpr.pdl), layer: "pdl" },
        { id: "prev", label: "Prev close", price: round2(cpr.prevClose), layer: "prevClose" },
        { id: "tc", label: "CPR TC", price: round2(cpr.tc), layer: "cpr" },
        { id: "pivot", label: "CPR", price: round2(cpr.pivot), layer: "cpr" },
        { id: "bc", label: "CPR BC", price: round2(cpr.bc), layer: "cpr" },
      );
    }
  }
  const weeks = [...new Set(bars.map((bar) => istParts(bar.time).week))].sort();
  const prevWeek = [...weeks].reverse().find((week) => week < today.week) ?? null;
  if (prevWeek) {
    const rows = bars.filter((bar) => istParts(bar.time).week === prevWeek);
    lines.push(
      { id: "week-high", label: "Week high", price: round2(Math.max(...rows.map((bar) => bar.high))), layer: "week" },
      { id: "week-low", label: "Week low", price: round2(Math.min(...rows.map((bar) => bar.low))), layer: "week" },
    );
  }
  const orBars = bars.filter((bar) => {
    const parts = istParts(bar.time);
    return parts.day === today.day && parts.minutes >= 9 * 60 + 15 && parts.minutes < 9 * 60 + 45;
  });
  const orClosed = bars.some((bar) => {
    const parts = istParts(bar.time);
    return parts.day === today.day && parts.minutes >= 9 * 60 + 45;
  });
  if (orBars.length > 0 && orClosed) {
    lines.push(
      { id: "or-high", label: "OR high", price: round2(Math.max(...orBars.map((bar) => bar.high))), layer: "opening" },
      { id: "or-low", label: "OR low", price: round2(Math.min(...orBars.map((bar) => bar.low))), layer: "opening" },
    );
  }
  const low = Math.min(...bars.map((bar) => bar.low));
  const high = Math.max(...bars.map((bar) => bar.high));
  for (const price of roundLevels(low, high, last.close)) {
    lines.push({ id: `round-${price}`, label: String(price), price, layer: "round" });
  }
  return lines;
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}
