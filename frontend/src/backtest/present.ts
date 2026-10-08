/** Pure helpers for the backtest panel. No React, so the tests can run them directly. */

export const DIRTY_WARNING = "code not committed: may not reproduce";

export interface RunConfig {
  strategy: string;
  params: Record<string, number | string | boolean>;
  symbol: string;
  timeframe: string;
  start: string;
  end: string | null;
  sessions: string[];
  mode: "index" | "options";
  strike_offset: number;
  slippage_points: number;
  /** option-premium exits as fractions of the entry premium (0.3 = +30%); absent or null = off */
  premium_target_pct?: number | null;
  premium_stop_pct?: number | null;
  /** true: orders work one minute after the bar closes, as in paper. false or absent (runs saved before
   * 8 Oct 2026): from the bar's end. */
  live_timing?: boolean;
  kind?: "walk_forward" | "holdout";
  train_months?: number;
  test_months?: number;
  step_months?: number;
  min_trades?: number;
  max_combinations?: number;
  include_forward?: boolean;
}

export interface TradeRow {
  id: number;
  entry_time: number;
  exit_time: number;
  direction: string;
  net_pnl: number;
}

export type SortKey = "entry_time" | "exit_time" | "net_pnl" | "direction";

export function formFromRun(config: RunConfig): RunConfig {
  return {
    ...config,
    params: { ...config.params },
    sessions: [...config.sessions],
    live_timing: config.live_timing === true,
  };
}

export function isLiveTiming(config: Pick<RunConfig, "live_timing">): boolean {
  return config.live_timing === true;
}

export function warningLines(warnings: string[], gitDirty: boolean): string[] {
  const rest = warnings.filter((line) => line !== DIRTY_WARNING);
  return gitDirty || warnings.includes(DIRTY_WARNING) ? [DIRTY_WARNING, ...rest] : rest;
}

export function compareWarningLines(
  runs: { label: string; git_dirty: boolean; warnings: string[] }[],
): string[] {
  const lines: string[] = [];
  const dirty = (run: { git_dirty: boolean; warnings: string[] }): boolean =>
    run.git_dirty || run.warnings.includes(DIRTY_WARNING);
  if (runs.some(dirty)) lines.push(DIRTY_WARNING);
  for (const run of runs) {
    if (dirty(run)) lines.push(`${run.label}: ${DIRTY_WARNING}`);
    for (const line of run.warnings) {
      if (line !== DIRTY_WARNING) lines.push(`${run.label}: ${line}`);
    }
  }
  return lines;
}

export function sortTrades<T extends TradeRow>(rows: readonly T[], key: SortKey, dir: "asc" | "desc"): T[] {
  const sign = dir === "asc" ? 1 : -1;
  return rows
    .map((row, index) => ({ row, index }))
    .sort((a, b) => {
      const left = a.row[key];
      const right = b.row[key];
      if (left < right) return -1 * sign;
      if (left > right) return 1 * sign;
      return a.index - b.index;
    })
    .map((item) => item.row);
}

export function jumpWindow(
  times: readonly number[],
  entryTime: number,
  visible = 120,
): { kind: "range"; from: number; to: number } | { kind: "load"; center: number } {
  const index = times.indexOf(entryTime);
  if (index < 0) return { kind: "load", center: entryTime };
  let from = index - visible / 3;
  let to = from + visible;
  if (from < 0) {
    to -= from;
    from = 0;
  }
  if (to > times.length) {
    from = Math.max(0, from - (to - times.length));
    to = times.length;
  }
  return { kind: "range", from, to };
}

export function compareSelection(
  ids: readonly string[],
): { ok: true; ids: string[] } | { ok: false; reason: string } {
  const unique = [...new Set(ids)];
  if (unique.length !== ids.length) return { ok: false, reason: "a run is selected twice" };
  if (unique.length < 2 || unique.length > 3) return { ok: false, reason: "choose 2 or 3 runs" };
  return { ok: true, ids: unique };
}

export function runLabel(
  config: Pick<RunConfig, "strategy" | "slippage_points" | "mode" | "live_timing"> & { kind?: string },
): string {
  const name =
    config.strategy === "opening_range_breakout"
      ? "ORB"
      : config.strategy === "ema_crossover"
        ? "EMA"
        : config.strategy === "supertrend_flip"
          ? "Supertrend"
          : config.strategy === "pivot_extension"
            ? "Pivot"
            : config.strategy === "log_xz"
              ? "Log XZ"
              : config.strategy === "price_channel"
                ? "Channel"
                : config.strategy;
  const slip = config.mode === "options" ? ` ${config.slippage_points} pt` : "";
  const prefix = config.kind === "walk_forward" ? "WF " : "";
  const timing = isLiveTiming(config) ? "" : " bar-end";
  return `${prefix}${name}${slip}${timing}`;
}

export function HOLDOUT_COUNT(peeks: number): string {
  return `final holdout has been run ${peeks} times`;
}

/** Frozen in backend/app/backtest/data/holdout.json. Research ends the day before. */
export const HOLDOUT_START = "2026-07-01";
export const HOLDOUT_END = "2026-10-01";
export const RESEARCH_END = "2026-06-30";

export function holdoutFormWarning(start: string, end: string | null): string | null {
  if (!start || (end !== null && end < start)) return null;
  const rangeEnd = end ?? "9999-12-31";
  if (start <= HOLDOUT_END && rangeEnd >= HOLDOUT_START) {
    return `This range overlaps the fixed holdout ${HOLDOUT_START} to ${HOLDOUT_END}. Research runs should end on ${RESEARCH_END}.`;
  }
  return null;
}

export function compareSeries(result: {
  kind?: string;
  equity?: { option?: { exit_time: number; equity: number; drawdown: number }[]; index?: { exit_time: number; equity: number; drawdown: number }[] };
}): { exit_time: number; equity: number; drawdown: number }[] {
  if (result.kind === "walk_forward") return result.equity?.option ?? [];
  const option = result.equity?.option ?? [];
  return option.length > 0 ? option : (result.equity?.index ?? []);
}

export function gridHint(strategy: string): string {
  if (strategy === "ema_crossover") return "Grid: fast 5, 9, 12 × slow 15, 21, 34";
  if (strategy === "supertrend_flip") return "Grid: ATR 7, 10, 14 × multiplier 2, 3, 4";
  if (strategy === "opening_range_breakout") return "Grid: range 5, 15, 30 minutes";
  if (strategy === "pivot_extension") return "Grid: faithful and carried_pivots × 5m and 15m";
  if (strategy === "log_xz") return "Grid: length 10, 14 × 5m and 15m";
  if (strategy === "price_channel") return "Grid: length 20, 40 × 5m and 15m";
  return "Grid: strategy defaults";
}

/** A percent typed in the form ("30") as the fraction a run takes (0.3); blank or not positive = off (null). */
export function percentToFraction(text: string): number | null {
  const n = Number(text);
  return text.trim() === "" || !Number.isFinite(n) || n <= 0 ? null : n / 100;
}

/** The fraction a run holds, shown as a percent in the form ("" when off). */
export function fractionToPercent(value: number | null | undefined): string {
  return value == null ? "" : String(Math.round(value * 10000) / 100);
}
