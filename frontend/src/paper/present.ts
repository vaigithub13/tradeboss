import type { SeriesMarker, Time, UTCTimestamp } from "lightweight-charts";

/** One entry of the paper signals log (see backend app/paper/session.py). */
export interface PaperSignal {
  time: number | null;
  decided_at_ms: number;
  side: "BUY" | "SELL" | "EXIT";
  index_price: number | null;
  reason: string;
  symbol: string | null;
  status: "filled" | "unfilled" | "skipped" | "exit";
  fill_source: "quote" | "modelled" | null;
  fill_price: number | null;
  note: string;
}

/** Open paper P&L at the live bid, or null when there is no position or no bid. */
export interface PaperMark {
  symbol: string;
  source: "quote" | null;
  gross: number | null;
  net: number | null;
}

const IST = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", hour12: false });

export function istHhmm(unixSeconds: number): string {
  return IST.format(new Date(unixSeconds * 1000));
}

/** "NIFTY 22600 CE 06 OCT 26" -> "22600 CE" */
export function shortContract(symbol: string | null): string {
  if (!symbol) return "";
  const parts = symbol.split(" ");
  return parts.length >= 3 ? `${parts[1]} ${parts[2]}` : symbol;
}

/** The strike alone, for a chart label that must stay short: "NIFTY 22600 CE 06 OCT 26" -> "22600" */
export function strikeOf(symbol: string | null): string {
  return symbol ? symbol.split(" ")[1] ?? "" : "";
}

/** Entry markers for the filled signals, snapped to the candle that contains each one. */
export function paperMarkers(
  signals: readonly PaperSignal[],
  candleTimes: readonly number[],
  prefix = "",
): SeriesMarker<Time>[] {
  const out: SeriesMarker<Time>[] = [];
  for (const s of signals) {
    if (s.status !== "filled" || s.time == null || (s.side !== "BUY" && s.side !== "SELL")) continue;
    const bar = snapToCandle(s.time, candleTimes);
    if (bar == null) continue;
    const buy = s.side === "BUY";
    out.push({
      time: bar as UTCTimestamp,
      position: buy ? "belowBar" : "aboveBar",
      shape: buy ? "arrowUp" : "arrowDown",
      color: s.fill_source === "modelled" ? "#eab308" : buy ? "#26a69a" : "#ef5350",
      text: `${prefix ? `${prefix} ` : ""}${buy ? "L" : "S"} ${strikeOf(s.symbol)}`,
    });
  }
  return out;
}

function snapToCandle(time: number, candleTimes: readonly number[]): number | null {
  let snapped: number | null = null;
  for (const t of candleTimes) {
    if (t <= time) snapped = t;
    else break;
  }
  return snapped;
}

export interface PaperRow {
  time: string;
  side: string;
  index: string;
  contract: string;
  fill: string;
  note: string;
}

/** The signals log, newest first. */
export function paperRows(signals: readonly PaperSignal[]): PaperRow[] {
  return [...signals].reverse().map((s) => ({
    time: s.time == null ? "—" : istHhmm(s.time),
    side: s.side,
    index: s.index_price == null ? "—" : s.index_price.toFixed(2),
    contract: shortContract(s.symbol) || "—",
    fill:
      s.fill_price == null
        ? "—"
        : `${s.fill_price.toFixed(2)} ${s.fill_source === "modelled" ? "(modelled)" : "(quote)"}`,
    note: s.status === "filled" ? s.reason : s.note || s.status,
  }));
}

/** "+1,234.50", "−88.00", or "—" */
export function formatRupees(value: number | null): string {
  if (value == null) return "—";
  const abs = Math.abs(value).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return value < 0 ? `−${abs}` : `+${abs}`;
}

export function markLine(mark: PaperMark | null): string {
  if (!mark) return "no open position";
  if (mark.net == null) return `${shortContract(mark.symbol)}: no live bid`;
  return `${shortContract(mark.symbol)}: ${formatRupees(mark.net)} net (at bid)`;
}

/** The params box of a paper slot: blank is {}, otherwise a JSON object. */
export function parseParams(text: string): { params?: Record<string, unknown>; error?: string } {
  if (text.trim() === "") return { params: {} };
  try {
    const value: unknown = JSON.parse(text);
    if (value === null || typeof value !== "object" || Array.isArray(value)) return { error: "params must be a JSON object" };
    return { params: value as Record<string, unknown> };
  } catch {
    return { error: "params are not valid JSON" };
  }
}

export const EXIT_RULES = [
  { name: "", label: "no stop / target" },
  { name: "premium_1to2", label: "1:2 premium (−20% / +40%)" },
  { name: "atr_1to2", label: "1:2 index ATR(14) (1x / 2x)" },
] as const;

/** What each slot starts with: 1 and 2 are the week's comparison (no exits); 3 and 4 the same with 1:2 premium. */
export const SLOT_DEFAULTS: Record<string, { strategy: string; exit: string }> = {
  "1": { strategy: "log_xz", exit: "" },
  "2": { strategy: "price_channel", exit: "" },
  "3": { strategy: "log_xz", exit: "premium_1to2" },
  "4": { strategy: "price_channel", exit: "premium_1to2" },
};

/** The params sent to start a slot: the box's params, plus `exit_rule` when one is chosen. */
export function startParams(params: Record<string, unknown>, exit: string): Record<string, unknown> {
  return exit ? { ...params, exit_rule: exit } : { ...params };
}

/** "1:2 premium (−20% / +40%)" for a running slot's params, or "" */
export function exitLabel(params: Record<string, unknown> | null | undefined): string {
  const name = params?.exit_rule;
  return EXIT_RULES.find((r) => r.name && r.name === name)?.label ?? "";
}
