/** What the live badge shows (pure; the component only renders it). */
import type { LinkState } from "./client";
import type { LiveStatus } from "./protocol";

export type Tone = "ok" | "warn" | "bad" | "idle";

export interface BadgeView {
  label: string;
  tone: Tone;
}

/**
 * Staleness is measured with the LOCAL clock for the badge only (bars never use it): the server's
 * `since_last_tick_s` as of the status message, plus the time since that message arrived.
 */
export function badgeView(status: LiveStatus | null, link: LinkState, statusAt: number | null, now: number): BadgeView {
  if (link !== "open" || !status) return { label: "reconnecting…", tone: "warn" };
  switch (status.state) {
    case "live":
      return { label: "live", tone: "ok" };
    case "stale": {
      const base = status.since_last_tick_s ?? 0;
      const extra = statusAt === null ? 0 : Math.max(0, (now - statusAt) / 1000);
      return { label: `stale (${Math.round(base + extra)}s since last tick)`, tone: "warn" };
    }
    case "reconnecting":
      return { label: "reconnecting…", tone: "warn" };
    case "closed":
      return { label: "market closed", tone: "idle" };
    case "auth_failed":
      return { label: "feed auth failed", tone: "bad" };
    case "locked":
      return { label: "feed in use elsewhere", tone: "bad" };
    case "disabled":
      return { label: "live feed off", tone: "idle" };
  }
}
