import type { HorizonSummary, Reliability } from "../api/similar";

/** No colour until there are 10 samples. A coin-flip verdict stays neutral. */
export function verdictClass(n: number, verdict: string): string {
  if (n < 10) return "text-white/70";
  if (verdict === "above the base rate") return "text-emerald-300";
  if (verdict === "below the base rate") return "text-rose-300";
  return "text-white/70";
}

export function fmtPct(value: number | null): string {
  return value == null ? "—" : `${value.toFixed(0)}%`;
}

export function summaryLine(horizon: string, block: HorizonSummary): string {
  const matched = block.matches;
  const base = block.base;
  if (!matched.n || matched.pct_up == null) return `${horizon}: no completed forwards`;
  const median = matched.median_return == null ? "—" : `${matched.median_return.toFixed(2)}%`;
  const spread = matched.spread == null ? "—" : matched.spread.toFixed(2);
  return `${horizon} candles: ${matched.pct_up.toFixed(0)}% up (n=${matched.n}) vs base ${fmtPct(base.pct_up)} of ${base.n}. Median ${median}, spread ${spread}.`;
}

export function reliabilityLine(block: Reliability | undefined): string {
  if (!block) return "Walk-forward has not been run for this symbol.";
  const hit = fmtPct(block.hit_rate);
  const base = fmtPct(block.base_rate);
  const cap = block.capped ? " Time cap hit." : "";
  return `Hit ${hit} vs base ${base} on ${block.n} calls (${block.evaluated}/${block.sample_size} sampled, ${block.runtime_s.toFixed(1)}s). ${block.verdict}.${cap}`;
}

const IST = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata",
  day: "2-digit",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

export function istStamp(seconds: number): string {
  return IST.format(seconds * 1000);
}

export const POINTS_LABEL = "Hypothetical index points. No costs. Not a trade.";
export const REVEAL_STEPS = [5, 10, 20] as const;

export function predictionLabel(upCount: number, matchN: number): string {
  if (matchN <= 0) return "no match";
  return `${upCount}/${matchN} up`;
}

export function nextRevealCount(shown: number): number | null {
  if (shown < 5) return 5;
  if (shown < 10) return 10;
  if (shown < 20) return 20;
  return null;
}

/** The time of the Nth later candle, or null when that candle is not closed yet. */
export function forwardEndTime(times: number[], end: number, count: number, limit: number | null): number | null {
  const start = times.indexOf(end);
  if (start < 0) return null;
  const last = start + count;
  if (last >= times.length) return null;
  const stamp = times[last];
  if (stamp == null || (limit != null && stamp > limit)) return null;
  return stamp;
}

export interface SnapshotMark {
  time: number;
  direction: "up" | "down" | "none";
  text: string;
}

export function snapshotMarkers(
  rows: { query_end?: number; query?: { end: number }; direction: "up" | "down" | "none"; up_count: number; match_n: number; kind: string }[],
): SnapshotMark[] {
  const byTime = new Map<number, typeof rows>();
  for (const row of rows) {
    const time = row.query_end ?? row.query?.end;
    if (time == null) continue;
    const list = byTime.get(time) ?? [];
    list.push(row);
    byTime.set(time, list);
  }
  return [...byTime.entries()].map(([time, group]) => {
    const candle = group.find((row) => row.kind === "candles") ?? group[0]!;
    return { time, direction: candle.direction, text: predictionLabel(candle.up_count, candle.match_n) };
  });
}

export function reviewLine(block: { predictions: number; hits: number; hit_rate: number | null; base_rate: number | null; points: number; verdict: string | null }): string {
  const hit = block.hit_rate == null ? "—" : `${block.hit_rate.toFixed(1)}%`;
  const base = block.base_rate == null ? "—" : `${block.base_rate.toFixed(1)}%`;
  const verdict = block.predictions >= 50 && block.verdict ? ` ${block.verdict}.` : "";
  return `${block.predictions} predictions, ${block.hits} hits, hit ${hit} vs base ${base}, ${block.points.toFixed(1)} pts.${verdict}`;
}
