import type { Candle, Timeframe } from "../api/client";
import { hasVolume } from "../chart/volume";
import { indicatorName, unavailableReason, type IndicatorInstance } from "./catalog";
import { valueAt, type IndicatorEntry } from "./cache";

export interface LegendEntry {
  label: string;
  text: string;
  color: string;
}

export interface LegendRow {
  id: string;
  name: string;
  /** set when the indicator cannot be shown now (e.g. VWAP on a zero-volume symbol) */
  unavailable: string | null;
  entries: LegendEntry[];
}

const num = new Intl.NumberFormat("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmt = (v: number | null | undefined): string => (v === null || v === undefined ? "–" : num.format(v));

/**
 * One legend row per visible indicator with its value at the candle starting at `time`
 * (the caller passes the last candle when the crosshair is not on the chart).
 */
export function legendRows(args: {
  items: readonly IndicatorInstance[];
  /** cached values for an item (undefined = not loaded yet) */
  entryFor: (item: IndicatorInstance) => IndicatorEntry | undefined;
  candles: readonly Candle[];
  timeframe: Timeframe;
  time: number | null;
  symbol?: string | null;
}): LegendRow[] {
  const { items, entryFor, candles, timeframe, time, symbol } = args;
  const volume = hasVolume(candles);
  const rows: LegendRow[] = [];
  for (const item of items) {
    if (!item.visible) continue;
    const row: LegendRow = {
      id: item.id,
      name: indicatorName(item),
      unavailable: unavailableReason(item.type, { timeframe, hasVolume: volume, symbol }),
      entries: [],
    };
    const entry = row.unavailable || time === null ? undefined : entryFor(item);
    if (entry && time !== null) {
      const at = (key: string): number | null => valueAt(entry, key, time);
      const c = item.colors;
      const color = (k: string): string => c[k] ?? "#d1d4dc";
      switch (item.type) {
        case "sma":
        case "ema":
        case "vwap":
        case "vwap_fut":
        case "rsi":
          row.entries.push({ label: "", text: fmt(at(item.type === "vwap_fut" ? "vwap" : item.type)), color: color("line") });
          break;
        case "stoch":
          row.entries.push(
            { label: "%K", text: fmt(at("k")), color: color("k") },
            { label: "%D", text: fmt(at("d")), color: color("d") },
          );
          break;
        case "bb":
          row.entries.push(
            { label: "basis", text: fmt(at("basis")), color: color("basis") },
            { label: "upper", text: fmt(at("upper")), color: color("band") },
            { label: "lower", text: fmt(at("lower")), color: color("band") },
          );
          break;
        case "supertrend": {
          const dir = at("direction");
          row.entries.push({
            label: dir === null ? "" : dir === -1 ? "up" : "down",
            text: fmt(at("supertrend")),
            color: dir === -1 ? color("up") : color("down"),
          });
          break;
        }
        case "macd": {
          const hist = at("hist");
          row.entries.push(
            { label: "macd", text: fmt(at("macd")), color: color("macd") },
            { label: "signal", text: fmt(at("signal")), color: color("signal") },
            { label: "hist", text: fmt(hist), color: hist !== null && hist < 0 ? color("histDown") : color("histUp") },
          );
          break;
        }
        case "fvg":
          break;
      }
    }
    rows.push(row);
  }
  return rows;
}
