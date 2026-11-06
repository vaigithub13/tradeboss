import type { GexResponse, GexStrike } from "../api/gex";

export const GEX_POS = "#26a69a";
export const GEX_NEG = "#ef5350";
export const GEX_FLIP = "#f6c343";

/** "+1,234 Cr", "−5.67 Cr" */
export function formatCrore(value: number): string {
  const a = Math.abs(value);
  const digits = a >= 100 ? 0 : a >= 10 ? 1 : 2;
  const text = a.toLocaleString("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return `${value < 0 ? "−" : "+"}${text} Cr`;
}

export interface GexBar {
  price: number;
  /** |net| as a share of the largest |net| among the bars drawn (0..1] */
  frac: number;
  positive: boolean;
}

/** Net GEX bars for the strikes within ±range of spot, scaled to the biggest one. */
export function gexBars(strikes: readonly GexStrike[], spot: number, range = 0.04): GexBar[] {
  const near = strikes.filter((s) => Math.abs(s.strike - spot) <= spot * range && s.net !== 0);
  const max = Math.max(0, ...near.map((s) => Math.abs(s.net)));
  if (max === 0) return [];
  return near.map((s) => ({ price: s.strike, frac: Math.abs(s.net) / max, positive: s.net > 0 }));
}

/** Price lines for the chart: gamma flip, call wall, put wall. */
export function gexLevels(g: GexResponse): { price: number; title: string; color: string }[] {
  const out: { price: number; title: string; color: string }[] = [];
  if (g.gamma_flip != null) out.push({ price: g.gamma_flip, title: "γ flip", color: GEX_FLIP });
  if (g.call_wall != null) out.push({ price: g.call_wall, title: "call wall", color: GEX_POS });
  if (g.put_wall != null) out.push({ price: g.put_wall, title: "put wall", color: GEX_NEG });
  return out;
}

function clock(unixSeconds: number): string {
  return new Date(unixSeconds * 1000).toLocaleTimeString("en-IN", {
    timeZone: "Asia/Kolkata",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

/** One line for the GEX badge. */
export function gexSummary(g: GexResponse): string {
  if (!g.available) return `GEX unavailable${g.reason ? `: ${g.reason}` : ""}`;
  const parts = [`Net GEX ${formatCrore(g.total_net ?? 0)}`];
  parts.push(g.regime === "negative" ? "short gamma: moves can trend" : "long gamma: moves tend to pin");
  if (g.gamma_flip != null) parts.push(`flip ${Math.round(g.gamma_flip).toLocaleString("en-IN")}`);
  parts.push(`exp ${g.expiry}`);
  parts.push(clock(g.as_of));
  return parts.join(" · ");
}
