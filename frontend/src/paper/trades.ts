/** The paper Trades view: one row per trade over the slots (backend GET /api/paper/trades). Pure helpers. */

import { defaultStyle, type Drawing } from "../draw/model";
import { defaultPositionSettings } from "../draw/position";
import type { FixedOutcome } from "../draw/positionFeed";
import type { ReportRow } from "../report/present";

export interface PaperTradeRow extends ReportRow {
  date: string;
  slot: string;
  side: "LONG" | "SHORT";
  /** CE or PE */
  direction: string;
  entry_time: number;
  index_entry: number | null;
  index_exit: number | null;
  lots: number;
  lot_size: number;
  nifty_points: number | null;
  premium_points: number;
  gross: number;
  charges: number;
  source: string;
  /** "entry" / "exit": the Nifty level came from the stored 1m candles (older trades) */
  index_from_candles: string[];
}

export type ColumnKey =
  | "date" | "slot" | "direction" | "signal" | "trigger" | "entryTime" | "niftyEntry" | "contract" | "lots"
  | "entryPremium" | "stop" | "target" | "exitTime" | "niftyExit" | "exitPremium" | "niftyPoints"
  | "premiumPoints" | "gross" | "charges" | "net" | "r" | "reason";

export const COLUMNS: { key: ColumnKey; label: string; numeric?: boolean }[] = [
  { key: "date", label: "Date" },
  { key: "slot", label: "Slot" },
  { key: "direction", label: "Direction" },
  { key: "signal", label: "Signal bar" },
  { key: "trigger", label: "Trigger Nifty", numeric: true },
  { key: "entryTime", label: "Entry time" },
  { key: "niftyEntry", label: "Nifty at entry", numeric: true },
  { key: "contract", label: "Contract" },
  { key: "lots", label: "Lots x qty" },
  { key: "entryPremium", label: "Entry premium", numeric: true },
  { key: "stop", label: "Stop: Nifty / premium", numeric: true },
  { key: "target", label: "Target: Nifty / premium", numeric: true },
  { key: "exitTime", label: "Exit time" },
  { key: "niftyExit", label: "Nifty at exit", numeric: true },
  { key: "exitPremium", label: "Exit premium", numeric: true },
  { key: "niftyPoints", label: "Nifty points", numeric: true },
  { key: "premiumPoints", label: "Premium points", numeric: true },
  { key: "gross", label: "Gross", numeric: true },
  { key: "charges", label: "Charges", numeric: true },
  { key: "net", label: "Net", numeric: true },
  { key: "r", label: "R", numeric: true },
  { key: "reason", label: "Exit reason" },
];

const TIME = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
});

export function hhmmss(unixSeconds: number | null): string {
  return unixSeconds == null ? "—" : TIME.format(new Date(unixSeconds * 1000));
}

function n2(v: number | null | undefined): string {
  return v == null ? "—" : v.toFixed(2);
}

function signed(v: number | null | undefined): string {
  return v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}`;
}

/** "~22571.55 / 101.00": the estimated side carries "~" */
function pair(index: number | null, premium: number | null, estimated: string[]): string {
  if (index == null && premium == null) return "—";
  const i = index == null ? "—" : `${estimated.includes("index") ? "~" : ""}${index.toFixed(2)}`;
  const p = premium == null ? "—" : `${estimated.includes("premium") ? "~" : ""}${premium.toFixed(2)}`;
  return `${i} / ${p}`;
}

/** The text of every cell, in COLUMNS order. */
export function cellText(row: PaperTradeRow, key: ColumnKey): string {
  switch (key) {
    case "date": return row.date;
    case "slot": return row.slot;
    case "direction": return row.direction;
    case "signal": return hhmmss(row.signal_time);
    case "trigger": return n2(row.trigger_index);
    case "entryTime": return hhmmss(row.entry_time);
    case "niftyEntry": return `${row.index_from_candles.includes("entry") ? "≈" : ""}${n2(row.index_entry)}`;
    case "contract": return row.contract.replace(/^NIFTY /, "");
    case "lots": return `${row.lots} x ${row.lot_size}`;
    case "entryPremium": return `${n2(row.entry_premium)} ${row.entry_source}`;
    case "stop": return pair(row.index_stop, row.premium_stop, row.estimated);
    case "target": return pair(row.index_target, row.premium_target, row.estimated);
    case "exitTime": return hhmmss(row.exit_time);
    case "niftyExit": return `${row.index_from_candles.includes("exit") ? "≈" : ""}${n2(row.index_exit)}`;
    case "exitPremium": return n2(row.exit_premium);
    case "niftyPoints": return signed(row.nifty_points);
    case "premiumPoints": return signed(row.premium_points);
    case "gross": return n2(row.gross);
    case "charges": return n2(row.charges);
    case "net": return n2(row.net);
    case "r": return row.r_multiple == null ? "—" : `${row.r_multiple >= 0 ? "+" : ""}${row.r_multiple.toFixed(2)}`;
    case "reason": return row.exit_reason ?? "—";
  }
}

function sortValue(row: PaperTradeRow, key: ColumnKey): number | string | null {
  switch (key) {
    case "date": return `${row.date} ${row.entry_time}`;
    case "slot": return row.slot;
    case "direction": return row.direction;
    case "signal": return row.signal_time;
    case "trigger": return row.trigger_index;
    case "entryTime": return row.entry_time;
    case "niftyEntry": return row.index_entry;
    case "contract": return row.contract;
    case "lots": return row.lots * row.lot_size;
    case "entryPremium": return row.entry_premium;
    case "stop": return row.index_stop ?? row.premium_stop;
    case "target": return row.index_target ?? row.premium_target;
    case "exitTime": return row.exit_time;
    case "niftyExit": return row.index_exit;
    case "exitPremium": return row.exit_premium;
    case "niftyPoints": return row.nifty_points;
    case "premiumPoints": return row.premium_points;
    case "gross": return row.gross;
    case "charges": return row.charges;
    case "net": return row.net;
    case "r": return row.r_multiple;
    case "reason": return row.exit_reason;
  }
}

/** Sorted copy; empty values go last either way. */
export function sortRows(rows: readonly PaperTradeRow[], key: ColumnKey, dir: "asc" | "desc"): PaperTradeRow[] {
  const sign = dir === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    const x = sortValue(a, key);
    const y = sortValue(b, key);
    if (x == null && y == null) return 0;
    if (x == null) return 1;
    if (y == null) return -1;
    if (typeof x === "number" && typeof y === "number") return (x - y) * sign;
    return String(x).localeCompare(String(y)) * sign;
  });
}

export interface TradeFilter {
  slot: string; // "" = all
  from: string; // YYYY-MM-DD or ""
  to: string;
}

export function filterRows(rows: readonly PaperTradeRow[], f: TradeFilter): PaperTradeRow[] {
  return rows.filter((r) => (!f.slot || r.slot === f.slot) && (!f.from || r.date >= f.from) && (!f.to || r.date <= f.to));
}

export interface Totals {
  trades: number;
  wins: number;
  net: number;
  avgR: number | null;
  exits: Record<"target" | "stop" | "reversal" | "square-off" | "other", number>;
}

export function totals(rows: readonly PaperTradeRow[]): Totals {
  const exits = { target: 0, stop: 0, reversal: 0, "square-off": 0, other: 0 };
  let net = 0;
  let wins = 0;
  const rs: number[] = [];
  for (const r of rows) {
    net += r.net ?? 0;
    if ((r.net ?? 0) > 0) wins += 1;
    if (r.r_multiple != null) rs.push(r.r_multiple);
    const reason = r.exit_reason ?? "";
    if (reason === "target" || reason === "stop" || reason === "reversal" || reason === "square-off") exits[reason] += 1;
    else exits.other += 1;
  }
  return {
    trades: rows.length,
    wins,
    net: Math.round(net * 100) / 100,
    avgR: rs.length ? Math.round((rs.reduce((a, b) => a + b, 0) / rs.length) * 1000) / 1000 : null,
    exits,
  };
}

function csvCell(text: string): string {
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

/** CSV of the rows as shown (same columns and text), header first. */
export function toCsv(rows: readonly PaperTradeRow[]): string {
  const lines = [COLUMNS.map((c) => csvCell(c.label)).join(",")];
  for (const r of rows) lines.push(COLUMNS.map((c) => csvCell(cellText(r, c.key).replace(/^—$/, ""))).join(","));
  return `${lines.join("\n")}\n`;
}


export interface TradeOverlay {
  drawing: Drawing;
  fixed: FixedOutcome;
  markers: { time: number; kind: "entry" | "exit"; long: boolean }[];
}

/** The trade drawn like the Long/Short Position tool, from its stored levels (Nifty), closed at its stored exit.
 * Without index levels (no exit rule) the zones run from the entry to the exit's Nifty level. Null without a
 * Nifty entry. */
export function tradeOverlay(row: PaperTradeRow): TradeOverlay | null {
  if (row.index_entry == null) return null;
  const long = row.side === "LONG";
  const entry = row.index_entry;
  const exitTime = row.exit_time ?? row.entry_time + 300;
  let stop = row.index_stop;
  let target = row.index_target;
  if (stop == null || target == null) {
    const exit = row.index_exit ?? entry;
    const gained = long ? exit >= entry : exit <= entry;
    target = gained ? exit : entry;
    stop = gained ? entry : exit;
  }
  const tool = long ? "long_position" : "short_position";
  const drawing: Drawing = {
    id: `paper-trade-${row.slot}-${row.entry_time}`,
    tool,
    anchors: [
      { time: row.entry_time, price: entry },
      { time: Math.max(exitTime, row.entry_time + 60), price: target },
      { time: row.entry_time, price: stop },
    ],
    knownAt: row.entry_time,
    drawnOn: "",
    showOn: null,
    hidden: false,
    locked: true,
    text: "",
    style: defaultStyle(tool),
    position: { ...defaultPositionSettings(), lotSize: row.lot_size },
  };
  const net = row.net ?? 0;
  const centre = [
    `Slot ${row.slot} ${row.contract.replace(/^NIFTY /, "")}: ${row.exit_reason ?? "open"}`,
    `Net ${net >= 0 ? "+" : ""}${net.toFixed(2)}${row.r_multiple == null ? "" : `, ${row.r_multiple >= 0 ? "+" : ""}${row.r_multiple.toFixed(2)}R`}`,
  ];
  return {
    drawing,
    fixed: { status: net >= 0 ? "target" : "stop", endTime: exitTime, exitPrice: row.index_exit, pnlRupees: net, centre },
    markers: [
      { time: row.entry_time, kind: "entry", long },
      ...(row.exit_time == null ? [] : [{ time: row.exit_time, kind: "exit" as const, long }]),
    ],
  };
}
