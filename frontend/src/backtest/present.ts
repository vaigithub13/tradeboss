/** Pure helpers for the backtest panel. No React, so the tests can run them directly. */

export const DIRTY_WARNING = "code not committed: may not reproduce";

export interface RunConfig {
  strategy: string;
  params: Record<string, number | string>;
  symbol: string;
  timeframe: string;
  start: string;
  end: string | null;
  sessions: string[];
  mode: "index" | "options";
  strike_offset: number;
  slippage_points: number;
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
  };
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

export function runLabel(config: Pick<RunConfig, "strategy" | "slippage_points" | "mode">): string {
  const name =
    config.strategy === "opening_range_breakout"
      ? "ORB"
      : config.strategy === "ema_crossover"
        ? "EMA"
        : config.strategy === "supertrend_flip"
          ? "Supertrend"
          : config.strategy;
  const slip = config.mode === "options" ? ` ${config.slippage_points} pt` : "";
  return `${name}${slip}`;
}
