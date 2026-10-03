const IST_OFFSET_MS = 5.5 * 60 * 60 * 1000;
const OPEN_MINUTE = 9 * 60 + 15;
const CLOSE_MINUTE = 15 * 60 + 30;

export interface AutoSettings {
  language: string;
  auto: boolean;
  auto_minutes: number;
}

/** Weekday 09:15–15:30 IST. The close minute itself is after the session. */
export function marketHours(now: Date): boolean {
  const ist = new Date(now.getTime() + IST_OFFSET_MS);
  const weekday = ist.getUTCDay();
  if (weekday === 0 || weekday === 6) return false;
  const minutes = ist.getUTCHours() * 60 + ist.getUTCMinutes();
  return minutes >= OPEN_MINUTE && minutes < CLOSE_MINUTE;
}

/**
 * True when auto-analyse is on, the clock is inside market hours, and a full
 * interval has passed since `lastAt`. `lastAt` null does not fire: the clock
 * starts when the app opens.
 */
export function autoDue(now: Date, lastAt: number | null, intervalMinutes: number, enabled: boolean): boolean {
  if (!enabled || lastAt == null || !marketHours(now)) return false;
  return now.getTime() - lastAt >= intervalMinutes * 60_000;
}

export function autoLabel(settings: AutoSettings): string {
  if (!settings.auto) return "Auto-analyse off";
  return `Auto every ${settings.auto_minutes} minutes during market hours`;
}
