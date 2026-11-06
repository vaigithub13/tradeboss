import type { HistogramPoint, LinePoint } from "./seriesData";

export interface VixColors {
  low: string;
  mid: string;
  high: string;
}

export interface VixLevels {
  low: number;
  high: number;
}

/** Below `low`: calm. From `low` to `high`: rising. Above `high`: high volatility. */
export function vixZone(value: number, levels: VixLevels): "low" | "mid" | "high" {
  if (value > levels.high) return "high";
  if (value >= levels.low) return "mid";
  return "low";
}

export function vixColor(value: number, levels: VixLevels, colors: VixColors): string {
  return colors[vixZone(value, levels)];
}

/** The VIX line, each point coloured by its zone. */
export function vixLinePoints(
  times: readonly number[],
  values: readonly (number | null)[],
  levels: VixLevels,
  colors: VixColors,
): LinePoint[] {
  return times.map((time, i) => {
    const v = values[i];
    return v === null || v === undefined ? { time } : { time, value: v, color: vixColor(v, levels, colors) };
  });
}

/** Full-height columns behind the candles where VIX is above `high` (whitespace elsewhere). */
export function vixShadePoints(
  times: readonly number[],
  values: readonly (number | null)[],
  high: number,
  color: string,
  on: boolean,
): HistogramPoint[] {
  return times.map((time, i) => {
    const v = values[i];
    return on && v !== null && v !== undefined && v > high ? { time, value: 1, color } : { time };
  });
}

/** "+0.62" / "−0.30" */
export function formatVixChange(change: number | null): string {
  if (change === null || !Number.isFinite(change)) return "–";
  const sign = change > 0 ? "+" : change < 0 ? "−" : "";
  return `${sign}${Math.abs(change).toFixed(2)}`;
}
