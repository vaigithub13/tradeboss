/**
 * Time left until the current NSE candle closes.
 * Buckets align from 09:15 IST; the last bar of the day always ends at 15:30
 * (e.g. a 1h candle starting 15:15), even when that bar is shorter than the step.
 *
 * Note: draw/model `barStart` clamps away that last short bar (CLOSE − step), so
 * countdown uses its own open helper.
 */
import { sessionOpen, type SessionCalendar } from "../ai/auto";

const IST = 19_800;
const DAY = 86_400;
const OPEN_MIN = 9 * 60 + 15;
const CLOSE_MIN = 15 * 60 + 30;

const INTRADAY: Record<string, number> = { "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60 };

export interface CountdownCalendar {
  holidays: readonly string[];
  weekendSessions: readonly string[];
}

export type Countdown =
  | { kind: "closed"; text: "Closed" }
  | { kind: "open"; text: string; secondsLeft: number };

const CLOSED: Countdown = { kind: "closed", text: "Closed" };

/**
 * Open of the candle containing `time` (unix seconds).
 * Unlike draw `barStart`, includes the final short bar (e.g. 15:15 on 1h).
 */
export function candleOpenUnix(time: number, timeframe: string): number {
  const day = Math.floor((time + IST) / DAY);
  const minute = Math.floor(((time + IST) % DAY) / 60);
  if (timeframe === "1D") return day * DAY - IST + OPEN_MIN * 60;
  if (timeframe === "1W") {
    const monday = day - ((day + 3) % 7);
    return monday * DAY - IST + OPEN_MIN * 60;
  }
  const step = INTRADAY[timeframe] ?? 15;
  // CLOSE_MIN - 1 keeps 15:15–15:29 inside the last bucket (barStart uses CLOSE − step).
  const clamped = Math.min(Math.max(minute, OPEN_MIN), CLOSE_MIN - 1);
  const k = Math.floor((clamped - OPEN_MIN) / step);
  return day * DAY - IST + (OPEN_MIN + k * step) * 60;
}

/** Unix seconds when the candle that opened at `barOpen` closes (session close for the last bar). */
export function candleCloseUnix(barOpen: number, timeframe: string): number {
  const day = Math.floor((barOpen + IST) / DAY);
  const dayClose = day * DAY - IST + CLOSE_MIN * 60;
  if (timeframe === "1D" || timeframe === "1W") return dayClose;
  const step = INTRADAY[timeframe] ?? 15;
  return Math.min(barOpen + step * 60, dayClose);
}

export function formatCountdown(secondsLeft: number, timeframe: string): string {
  const s = Math.max(0, Math.floor(secondsLeft));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  // 1h and daily/weekly always use hh:mm:ss (TradingView-style for longer bars).
  if (timeframe === "1h" || timeframe === "1D" || timeframe === "1W" || h > 0) {
    return `${pad(h)}:${pad(m)}:${pad(sec)}`;
  }
  return `${pad(m)}:${pad(sec)}`;
}

function toSessionCalendar(calendar: CountdownCalendar): SessionCalendar {
  return {
    holidays: calendar.holidays,
    weekend_sessions: calendar.weekendSessions,
    muhurat: [],
  };
}

/**
 * Countdown to the close of the candle that contains `nowSec`.
 * Outside an open NSE session (or on a holiday): "Closed".
 */
export function candleCountdown(
  nowSec: number,
  timeframe: string,
  calendar: CountdownCalendar,
): Countdown {
  const now = new Date(nowSec * 1000);
  if (!sessionOpen(now, toSessionCalendar(calendar), "unknown")) return CLOSED;

  const open = candleOpenUnix(nowSec, timeframe);
  const close = candleCloseUnix(open, timeframe);
  if (nowSec >= close) return CLOSED;

  const secondsLeft = close - nowSec;
  return { kind: "open", text: formatCountdown(secondsLeft, timeframe), secondsLeft };
}
