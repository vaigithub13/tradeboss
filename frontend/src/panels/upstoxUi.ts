/** Pure presentation helpers for the Upstox UI (unit-tested; no React). */
import type { InstrumentHit, SyncJob, TokenStatus } from "../api/client";

export interface BadgeUi {
  label: string;
  dot: string;
  text: string;
  title: string;
}

/** Header badge for the data token: "data token valid / invalid / expired" (+ missing / unverified). */
export function tokenBadge(token: TokenStatus | null, checkFailed: boolean): BadgeUi {
  if (!token) {
    return checkFailed
      ? { label: "data token ?", dot: "bg-white/30", text: "text-white/50", title: "Could not ask the backend for the data token status" }
      : { label: "data token…", dot: "bg-yellow-400", text: "text-yellow-300", title: "Checking the Upstox data token" };
  }
  const soon = token.expires_soon && token.days_left !== null ? ` · ${token.days_left}d left` : "";
  switch (token.state) {
    case "valid":
      return {
        label: `data token valid${soon}`,
        dot: token.expires_soon ? "bg-yellow-400" : "bg-emerald-400",
        text: token.expires_soon ? "text-yellow-300" : "text-emerald-300",
        title: token.message,
      };
    case "invalid":
      return { label: "data token invalid", dot: "bg-red-500", text: "text-red-400", title: token.message };
    case "expired":
      return { label: "data token expired", dot: "bg-red-500", text: "text-red-400", title: token.message };
    case "missing":
      return { label: "data token missing", dot: "bg-yellow-400", text: "text-yellow-300", title: token.message };
    case "unreachable":
      return { label: "data token unverified", dot: "bg-yellow-400", text: "text-yellow-300", title: token.message };
  }
}

/** Is the Upstox data call usable right now? (Only a definitely bad token blocks fetching.) */
export const tokenBlocksFetching = (token: TokenStatus | null): boolean =>
  token !== null && (token.state === "invalid" || token.state === "expired" || token.state === "missing");

/** One-line progress / result text for a sync job. */
export function jobText(job: SyncJob): string {
  if (job.status === "error") return `failed: ${job.error ?? job.message}`;
  if (job.status === "done") return `done: ${job.bars_added.toLocaleString("en-US")} 1m bars`;
  return job.windows_total > 0 ? `fetching 1m history ${job.windows_done}/${job.windows_total}…` : "starting…";
}

/** Secondary text of a search result row. */
export function hitDetail(hit: InstrumentHit): string {
  if (hit.kind === "future" || hit.kind === "option") {
    return [hit.name, hit.expiry ? `exp ${hit.expiry}` : null, hit.lot_size ? `lot ${hit.lot_size}` : null].filter(Boolean).join(" · ");
  }
  return hit.name;
}

export type DataFlag = "1m" | "partial" | "fetch";

/** 1m = 1m history stored; partial = only coarser data stored; fetch = nothing stored (will be fetched). */
export const dataFlag = (hit: Pick<InstrumentHit, "has_data" | "has_1m">): DataFlag =>
  hit.has_1m ? "1m" : hit.has_data ? "partial" : "fetch";
