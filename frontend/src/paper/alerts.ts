import type { PaperStatus } from "../api/paper";

/** A paper trade event worth a sound: an option bought (entry) or sold (exit). */
export interface PaperAlert {
  kind: "entry" | "exit";
  slot: string;
  /** the option leg: bought on entry, sold on exit */
  action: "BUY" | "SELL";
  contract: string;
  price: number | null;
  /** exits only: target, stop, reversal, square-off, ... */
  reason?: string;
  /** exits only: the trade's net P&L in rupees */
  net?: number;
}

/** What one slot looked like at the last poll: the entries and exits already seen. */
export interface SlotMemory {
  day: string | null;
  running: boolean;
  entries: Set<string>;
  exits: Set<string>;
}

export type AlertMemory = Record<string, SlotMemory>;

/** The trade fields the status sends (backend app/paper/book.py); the API type lists only a few. */
interface TradeLike {
  symbol: string;
  net: number;
  exit_reason: string;
  exit_price?: number;
  entry_at_ms?: number;
  exit_at_ms?: number;
  source?: string;
}

interface SignalLike {
  decided_at_ms: number;
  side: string;
  status: string;
  symbol: string | null;
  fill_price: number | null;
  source?: string;
}

const REASONS: Record<string, string> = {
  premium_target: "target", index_target: "target", target: "target",
  premium_stop: "stop", index_stop: "stop",
  square_off: "square-off",
  reverse: "reversal", signal: "reversal", reversal: "reversal",
  session_end: "session end", end_of_data: "session end",
  exit: "exit signal",
  stop: "stopped by user",
};

export function exitReasonLabel(raw: string): string {
  return REASONS[raw] ?? raw.replace(/_/g, " ");
}

const entryKey = (s: SignalLike): string => `${s.decided_at_ms}|${s.side}`;
const exitKey = (t: TradeLike): string => `${t.entry_at_ms ?? ""}|${t.exit_at_ms ?? ""}|${t.symbol}`;
const isLive = (source: string | undefined): boolean => (source ?? "live") === "live";

/** Entries are filled BUY/SELL signals (a BUY signal buys a CE, a SELL signal a PE: either way an option is bought). */
function filledEntries(status: PaperStatus): SignalLike[] {
  return ((status.signals ?? []) as unknown as SignalLike[]).filter(
    (s) => (s.side === "BUY" || s.side === "SELL") && s.status === "filled",
  );
}

/**
 * The alerts between the last poll of a slot (`prev`) and this one, and the slot's new memory.
 *
 * Only live trades sound. Nothing sounds for:
 * - a replay run (a Start after the close), or a signal or trade marked `replay` (decided while a late Start caught up
 *   from the day's recording, before it went live);
 * - the first poll of a slot, a new day, or the first poll after the slot became running (whatever is already there
 *   was decided before this page saw the slot live: a reload, or a Start's catch-up);
 * - a slot switched off (`slotOn` false). Its memory still moves on, so switching it back on later does not replay
 *   what happened meanwhile.
 */
export function slotAlerts(prev: SlotMemory | undefined, status: PaperStatus, slotOn: boolean): {
  alerts: PaperAlert[];
  memory: SlotMemory;
} {
  const entries = filledEntries(status);
  const trades = (status.trades ?? []) as unknown as TradeLike[];
  const memory: SlotMemory = {
    day: status.day,
    running: status.state === "running",
    entries: new Set(entries.map(entryKey)),
    exits: new Set(trades.map(exitKey)),
  };
  const watching = prev !== undefined && prev.running && prev.day === status.day && status.day !== null;
  if (!watching || !slotOn || status.source === "replay") return { alerts: [], memory };

  const alerts: PaperAlert[] = [];
  for (const s of entries) {
    if (prev.entries.has(entryKey(s)) || !isLive(s.source)) continue;
    alerts.push({ kind: "entry", slot: status.slot, action: "BUY", contract: s.symbol ?? "", price: s.fill_price });
  }
  for (const t of trades) {
    if (prev.exits.has(exitKey(t)) || !isLive(t.source)) continue;
    alerts.push({
      kind: "exit", slot: status.slot, action: "SELL", contract: t.symbol, price: t.exit_price ?? null,
      reason: exitReasonLabel(t.exit_reason), net: t.net,
    });
  }
  // an entry and its exit inside one poll: the entry first
  alerts.sort((a, b) => (a.kind === b.kind ? 0 : a.kind === "entry" ? -1 : 1));
  return { alerts, memory };
}

/** Every slot of one poll: the alerts in slot order, and the memory for the next poll. */
export function deskAlerts(prev: AlertMemory, slots: PaperStatus[], slotOn: (slot: string) => boolean): {
  alerts: PaperAlert[];
  memory: AlertMemory;
} {
  const memory: AlertMemory = {};
  const alerts: PaperAlert[] = [];
  for (const status of slots) {
    const out = slotAlerts(prev[status.slot], status, slotOn(status.slot));
    memory[status.slot] = out.memory;
    alerts.push(...out.alerts);
  }
  return { alerts, memory };
}

const rupees = (v: number): string => {
  const abs = Math.abs(v).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return v < 0 ? `−₹${abs}` : `+₹${abs}`;
};

/** The browser notification for an alert. */
export function alertNotification(a: PaperAlert): { title: string; body: string } {
  const price = a.price == null ? "price n/a" : `@ ${a.price.toFixed(2)}`;
  if (a.kind === "entry") {
    return { title: `Slot ${a.slot} · BUY ${a.contract}`, body: `Entry ${price}` };
  }
  const net = a.net == null ? "" : ` · net ${rupees(a.net)}`;
  return { title: `Slot ${a.slot} · SELL ${a.contract}`, body: `Exit ${price} · ${a.reason ?? "exit"}${net}` };
}
