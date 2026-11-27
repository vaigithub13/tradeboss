/**
 * Learn-tab event calendar. Dates live in events.json so they can be edited.
 * Expiry weekdays are generated from the exchange circulars named on each event.
 * A date with verified:false has not been checked against that source.
 */

import calendarFile from "./events.json" with { type: "json" };

export type EventKind =
  | "holiday"
  | "expiry"
  | "rbi"
  | "cpi"
  | "gdp"
  | "iip"
  | "fomc"
  | "us_cpi"
  | "nfp"
  | "budget"
  | "other";

export interface CalendarEvent {
  date: string;
  kind: EventKind;
  title: string;
  symbols: string[];
  source: string;
  verified: boolean;
  markOn: "same" | "next_session";
}

interface CalendarFile {
  note: string;
  holidays: string[];
  holidaySource: string;
  events: CalendarEvent[];
}

const file = calendarFile as CalendarFile;
const HOLIDAYS = new Set(file.holidays);
const RANGE_FROM = "2022-01-01";
const RANGE_TO = "2026-12-31";

const NIFTY_EXPIRY_SOURCE =
  "Thursday through 28 Aug 2025, Tuesday from 2 Sep 2025. NSE/FAOP/68589 and NSE/FAOP/68747. If that day is a holiday, the previous trading day. Same rule as backend/app/backtest/data/expiry_rules.json.";
const SENSEX_EXPIRY_SOURCE =
  "Thursday from 4 Sep 2025. BSE notice 20250623-59 (23 Jun 2025). Earlier Sensex weekdays are not generated; add them in events.json. Holiday shifts use the NSE holiday list.";
const BANK_WEEKLY_SOURCE =
  "Wednesday weekly until 13 Nov 2024. Weekly Bank Nifty options stop after NSE/FAOP/64506 (from 20 Nov 2024). The Wednesday itself is the long-standing contract day and is marked unverified for each date.";
const BANK_MONTHLY_SOURCE =
  "Monthly Bank Nifty follows the Nifty weekday: last Thursday through Aug 2025, last Tuesday from Sep 2025 (NSE/FAOP/68747).";
const NFP_SOURCE =
  "BLS Employment Situation is usually released on the first Friday. A US holiday can move it. Each date is unverified. Shown on the next Indian session.";

function parse(day: string): Date {
  const [year, month, date] = day.split("-").map(Number);
  return new Date(Date.UTC(year ?? 1970, (month ?? 1) - 1, date ?? 1));
}

function iso(day: Date): string {
  return day.toISOString().slice(0, 10);
}

function addDays(day: string, count: number): string {
  const next = parse(day);
  next.setUTCDate(next.getUTCDate() + count);
  return iso(next);
}

function weekday(day: string): number {
  return parse(day).getUTCDay();
}

export function isHoliday(day: string): boolean {
  return HOLIDAYS.has(day) || weekday(day) === 0 || weekday(day) === 6;
}

function previousTradingDay(day: string): string {
  let cursor = day;
  while (isHoliday(cursor)) cursor = addDays(cursor, -1);
  return cursor;
}

function nextTradingDay(day: string): string {
  let cursor = addDays(day, 1);
  while (isHoliday(cursor)) cursor = addDays(cursor, 1);
  return cursor;
}

function eachWeekday(start: string, end: string, wd: number): string[] {
  let cursor = start;
  while (cursor <= end && weekday(cursor) !== wd) cursor = addDays(cursor, 1);
  const out: string[] = [];
  while (cursor <= end) {
    out.push(cursor);
    cursor = addDays(cursor, 7);
  }
  return out;
}

function lastWeekdayOfMonth(year: number, month: number, wd: number): string {
  const last = new Date(Date.UTC(year, month, 0));
  while (last.getUTCDay() !== wd) last.setUTCDate(last.getUTCDate() - 1);
  return iso(last);
}

function event(partial: CalendarEvent): CalendarEvent {
  return partial;
}

function generated(): CalendarEvent[] {
  const out: CalendarEvent[] = [];
  const named = new Set(file.events.filter((item) => item.kind === "holiday").map((item) => item.date));
  for (const day of file.holidays) {
    if (named.has(day)) continue;
    out.push(event({
      date: day,
      kind: "holiday",
      title: "Market holiday",
      symbols: [],
      source: file.holidaySource,
      verified: true,
      markOn: "same",
    }));
  }
  for (const day of eachWeekday("2022-01-06", "2025-08-28", 4)) {
    const date = previousTradingDay(day);
    out.push(event({
      date, kind: "expiry", title: "Nifty weekly expiry", symbols: ["NIFTY50"],
      source: NIFTY_EXPIRY_SOURCE, verified: true, markOn: "same",
    }));
  }
  for (const day of eachWeekday("2025-09-02", RANGE_TO, 2)) {
    const date = previousTradingDay(day);
    out.push(event({
      date, kind: "expiry", title: "Nifty weekly expiry", symbols: ["NIFTY50"],
      source: NIFTY_EXPIRY_SOURCE, verified: true, markOn: "same",
    }));
  }
  for (const day of eachWeekday("2025-09-04", RANGE_TO, 4)) {
    const date = previousTradingDay(day);
    out.push(event({
      date, kind: "expiry", title: "Sensex weekly expiry", symbols: ["SENSEX"],
      source: SENSEX_EXPIRY_SOURCE, verified: date === day, markOn: "same",
    }));
  }
  for (const day of eachWeekday("2022-01-05", "2024-11-13", 3)) {
    const date = previousTradingDay(day);
    out.push(event({
      date, kind: "expiry", title: "Bank Nifty weekly expiry", symbols: ["BANKNIFTY"],
      source: BANK_WEEKLY_SOURCE, verified: false, markOn: "same",
    }));
  }
  for (let year = 2022; year <= 2026; year += 1) {
    for (let month = 1; month <= 12; month += 1) {
      const tuesday = year > 2025 || (year === 2025 && month >= 9);
      const nominal = lastWeekdayOfMonth(year, month, tuesday ? 2 : 4);
      if (nominal < RANGE_FROM || nominal > RANGE_TO) continue;
      out.push(event({
        date: previousTradingDay(nominal),
        kind: "expiry",
        title: "Bank Nifty monthly expiry",
        symbols: ["BANKNIFTY"],
        source: BANK_MONTHLY_SOURCE,
        verified: nominal >= "2025-09-01",
        markOn: "same",
      }));
    }
  }
  for (let year = 2025; year <= 2026; year += 1) {
    for (let month = 1; month <= 12; month += 1) {
      const first = `${year}-${String(month).padStart(2, "0")}-01`;
      const friday = eachWeekday(first, addDays(first, 6), 5)[0];
      if (!friday) continue;
      out.push(event({
        date: friday, kind: "nfp", title: "US NFP (usually)", symbols: [],
        source: NFP_SOURCE, verified: false, markOn: "next_session",
      }));
    }
  }
  return out;
}

let cached: CalendarEvent[] | null = null;

function placed(item: CalendarEvent): CalendarEvent {
  if (item.markOn !== "next_session") return item;
  return { ...item, date: nextTradingDay(item.date) };
}

export function allEvents(): CalendarEvent[] {
  if (cached) return cached;
  const map = new Map<string, CalendarEvent>();
  for (const item of generated()) {
    const shown = placed(item);
    map.set(`${shown.date}|${shown.kind}|${shown.title}|${shown.symbols.join(",")}`, shown);
  }
  for (const item of file.events) {
    const shown = placed(item);
    map.set(`${shown.date}|${shown.kind}|${shown.title}|${shown.symbols.join(",")}`, shown);
  }
  cached = [...map.values()].sort((a, b) => a.date.localeCompare(b.date) || a.title.localeCompare(b.title));
  return cached;
}

export function eventsOn(day: string, symbol?: string): CalendarEvent[] {
  return allEvents().filter((item) => item.date === day && (item.symbols.length === 0 || symbol == null || item.symbols.includes(symbol)));
}

export function isExpiryDay(day: string, symbol: string): boolean {
  return eventsOn(day, symbol).some((item) => item.kind === "expiry");
}

export function todayLine(day: string, symbol: string): string {
  const items = eventsOn(day, symbol);
  if (items.length === 0) return "Today: no listed event.";
  const text = items.map((item) => item.verified ? item.title : `${item.title} (unverified)`).join(", ");
  return `Today: ${text}.`;
}

export const CALENDAR_NOTE = file.note;
