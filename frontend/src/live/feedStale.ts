import type { LiveStatus } from "./protocol";

const STALE_MS = 15_000;

/**
 * The pill is the Nifty feed, measured on the server. A quiet chart bar or a busy
 * main thread does not count: only "no NIFTY tick for 15 s while the market is open".
 */
export function feedIsStale(
  status: LiveStatus | null,
  statusAt: number | null,
  now: number,
  cursor: number | null,
): boolean {
  if (cursor != null || status?.market !== "open") return false;
  if (status.nifty_since_tick_s == null || statusAt == null) return false;
  const age = status.nifty_since_tick_s * 1000 + Math.max(0, now - statusAt);
  return age >= STALE_MS;
}
