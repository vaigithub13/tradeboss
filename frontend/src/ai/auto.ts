const IST_OFFSET_MS = 5.5 * 60 * 60 * 1000;
const OPEN_MINUTE = 9 * 60 + 15;
const CLOSE_MINUTE = 15 * 60 + 30;

export type MarketStatus = "open" | "closed" | "unknown";

/** Dates from the NSE holiday calendar, plus known weekend and Muhurat sessions. */
export interface SessionCalendar {
  holidays: readonly string[];
  weekend_sessions: readonly string[];
  muhurat: readonly string[];
}

export interface AutoSettings extends SessionCalendar {
  language: string;
  auto: boolean;
  auto_minutes: number;
}

function istParts(now: Date): { date: string; minutes: number; weekday: number } {
  const ist = new Date(now.getTime() + IST_OFFSET_MS);
  const month = String(ist.getUTCMonth() + 1).padStart(2, "0");
  const day = String(ist.getUTCDate()).padStart(2, "0");
  return {
    date: `${ist.getUTCFullYear()}-${month}-${day}`,
    minutes: ist.getUTCHours() * 60 + ist.getUTCMinutes(),
    weekday: ist.getUTCDay(),
  };
}

/**
 * Whether an analysis may run.
 * market_info "open" includes a budget weekend or Muhurat, at whatever time the exchange is open.
 * market_info "closed" stops the timer, including a holiday that falls on a weekday.
 * With no market_info, a weekday that is not an NSE holiday, or a known full weekend session,
 * counts from 09:15 until 15:30 IST. A Muhurat evening is not guessed from the clock.
 */
export function sessionOpen(now: Date, calendar: SessionCalendar, market: MarketStatus): boolean {
  if (market === "open") return true;
  if (market === "closed") return false;
  const { date, minutes, weekday } = istParts(now);
  if (minutes < OPEN_MINUTE || minutes >= CLOSE_MINUTE) return false;
  if (calendar.holidays.includes(date)) return false;
  if (calendar.weekend_sessions.includes(date)) return true;
  if (weekday === 0 || weekday === 6) return false;
  return true;
}

/**
 * True when auto-analyse is on, the session is open, and a full interval has
 * passed since `lastAt`. `lastAt` null does not fire: the clock starts when the app opens.
 */
export function autoDue(
  now: Date,
  lastAt: number | null,
  intervalMinutes: number,
  enabled: boolean,
  calendar: SessionCalendar,
  market: MarketStatus = "unknown",
): boolean {
  if (!enabled || lastAt == null || !sessionOpen(now, calendar, market)) return false;
  return now.getTime() - lastAt >= intervalMinutes * 60_000;
}

export function autoLabel(settings: AutoSettings): string {
  if (!settings.auto) return "Auto-analyse off";
  return `Auto every ${settings.auto_minutes} minutes during open sessions`;
}
