import type { MarketFactor, MarketLevel, MarketView } from "../api/marketView";

export const MV_UP = "#26a69a";
export const MV_DOWN = "#ef5350";
export const MV_FLAT = "#8b93a7";
export const MV_NA = "#3a3f4b";

export function factorColor(f: Pick<MarketFactor, "score" | "available">): string {
  if (!f.available || f.score === null) return MV_NA;
  return f.score > 0 ? MV_UP : f.score < 0 ? MV_DOWN : MV_FLAT;
}

export function biasColor(score: number): string {
  return score > 0 ? MV_UP : score < 0 ? MV_DOWN : MV_FLAT;
}

const LEVEL_COLORS: Record<string, string> = {
  pdh: "#26a69a",
  pdl: "#ef5350",
  tc: "#b388ff",
  bc: "#b388ff",
  range_hi: "#5b9cf6",
  range_lo: "#5b9cf6",
};

/** Price lines for the chart. */
export function viewLevels(levels: readonly MarketLevel[]): { price: number; title: string; color: string }[] {
  return levels.map((l) => ({ price: l.price, title: l.label, color: LEVEL_COLORS[l.key] ?? MV_FLAT }));
}

/** YYYY-MM-DD in IST. */
export function istDate(unixSeconds: number): string {
  return new Date((unixSeconds + 5.5 * 3600) * 1000).toISOString().slice(0, 10);
}

/** Minutes since 00:00 IST. */
export function istMinutes(unixSeconds: number): number {
  const d = new Date((unixSeconds + 5.5 * 3600) * 1000);
  return d.getUTCHours() * 60 + d.getUTCMinutes();
}

const OPEN_MIN = 9 * 60 + 15;

/** The full panel opens by itself before 09:15 IST unless it was collapsed today. */
export function shouldOpenPanel(nowSeconds: number, collapsedOn: string | null): boolean {
  if (collapsedOn === istDate(nowSeconds)) return false;
  return istMinutes(nowSeconds) < OPEN_MIN;
}

export function dayTypeLabel(view: Pick<MarketView, "day_type">): string {
  return view.day_type === "trend" ? "Trend day likely" : view.day_type === "range" ? "Range day likely" : "Day type unclear";
}

export function formatRange(range: [number, number] | null): string {
  if (!range) return "–";
  const f = (n: number) => Math.round(n).toLocaleString("en-IN");
  return `${f(range[0])}–${f(range[1])}`;
}

export function rateText(r: { n: number; rate: number | null }): string {
  return r.rate === null ? `– (0 days)` : `${Math.round(r.rate * 100)}% of ${r.n} days`;
}
