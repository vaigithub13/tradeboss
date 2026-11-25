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
