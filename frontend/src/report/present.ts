/** The trade report (backend app/exits/report.py): one row per trade, for paper and backtest results alike. */

export interface ReportRow {
  id?: number;
  direction: string;
  signal_time: number | null;
  trigger_index: number | null;
  fill_time: number;
  contract: string;
  entry_premium: number;
  entry_source: string;
  units: number;
  rule: string | null;
  index_stop: number | null;
  index_target: number | null;
  premium_stop: number | null;
  premium_target: number | null;
  /** which side's levels are estimated through the model delta: "index" or "premium" */
  estimated: string[];
  exit_time: number | null;
  exit_premium: number | null;
  exit_reason: string | null;
  exit_reason_raw: string | null;
  net: number | null;
  risk: number | null;
  r_multiple: number | null;
}

export type ExitCounts = Record<"target" | "stop" | "reversal" | "square-off" | "other", number>;

export interface ReportCells {
  signal: string;
  trigger: string;
  fill: string;
  contract: string;
  entry: string;
  indexLevels: string;
  premiumLevels: string;
  exit: string;
  exitPremium: string;
  reason: string;
  net: string;
  r: string;
}

const TIME = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
});
const DATE = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit" });

/** IST "HH:MM:SS", or "YYYY-MM-DD HH:MM:SS" with the date. */
export function istTime(unixSeconds: number | null, withDate = false): string {
  if (unixSeconds == null) return "—";
  const d = new Date(unixSeconds * 1000);
  return withDate ? `${DATE.format(d)} ${TIME.format(d)}` : TIME.format(d);
}

function num(v: number | null, digits = 2): string {
  return v == null ? "—" : v.toFixed(digits);
}

/** "stop / target", an estimated pair prefixed with "~" */
function levels(stop: number | null, target: number | null, estimated: boolean): string {
  if (stop == null && target == null) return "—";
  return `${estimated ? "~" : ""}${num(stop)} / ${num(target)}`;
}

/** "NIFTY 22500 PE 13 OCT 26" -> "22500 PE" */
function shortContract(symbol: string): string {
  const parts = symbol.split(" ");
  return parts.length >= 3 ? `${parts[1]} ${parts[2]}` : symbol;
}

export function reportCells(row: ReportRow, withDate = false): ReportCells {
  return {
    signal: istTime(row.signal_time, withDate),
    trigger: num(row.trigger_index),
    fill: istTime(row.fill_time, withDate),
    contract: shortContract(row.contract),
    entry: `${num(row.entry_premium)} ${row.entry_source}`,
    indexLevels: levels(row.index_stop, row.index_target, row.estimated.includes("index")),
    premiumLevels: levels(row.premium_stop, row.premium_target, row.estimated.includes("premium")),
    exit: istTime(row.exit_time, withDate),
    exitPremium: num(row.exit_premium),
    reason: row.exit_reason ?? "—",
    net: row.net == null ? "—" : row.net.toFixed(2),
    r: row.r_multiple == null ? "—" : `${row.r_multiple >= 0 ? "+" : ""}${row.r_multiple.toFixed(2)}R`,
  };
}

/** "target 1 · stop 2 · reversal 1 · square-off 3 · other 0" */
export function exitCountsLine(counts: Partial<ExitCounts> | null | undefined): string {
  const c = counts ?? {};
  return (["target", "stop", "reversal", "square-off", "other"] as const).map((k) => `${k} ${c[k] ?? 0}`).join(" · ");
}
