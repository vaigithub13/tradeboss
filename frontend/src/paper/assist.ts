/** Manual assist for one paper slot. Tickets and a journal. No order is built here. */

export const PREMIUM_STOP = 0.2;
export const PREMIUM_TARGET = 0.4;
export const TICK = 0.05;
export const ENTRY_WINDOW_S = 60;
export const TRAIL_WINDOW_S = 120;
export const PRACTICE_BARS = 6;
export const PRACTICE_TAGS = ["trend pullback", "level break", "range fade", "other"] as const;
export type PracticeTag = (typeof PRACTICE_TAGS)[number];
export const REMINDER_MINUTE = 15 * 60 + 10;
export const STORY_MINUTE = 15 * 60 + 30;
export const SKIP_REASONS = ["late", "missed", "afraid", "other"] as const;
export type SkipReason = (typeof SKIP_REASONS)[number];

export interface ChartCandle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
}

export interface ChartSnap {
  candles: ChartCandle[];
  channel_high: number | null;
  channel_low: number | null;
  signal_time: number | null;
  entry_time?: number | null;
  exit_time?: number | null;
}

export interface AssistTicket {
  key: string;
  slot: string;
  day: string;
  fill_time: number;
  decided_at_ms: number;
  time: string;
  action: string;
  option_side: string;
  signal_side: string;
  contract: string | null;
  bid: number | null;
  ask: number | null;
  ltp: number | null;
  suggested_limit: number | null;
  lots: number;
  lot_size: number;
  quantity: number;
  stop: number | null;
  target: number | null;
  stop_frac: number | null;
  target_frac: number | null;
  charges: number | null;
  max_loss: number | null;
  paper_fill: number | null;
  trailing?: boolean;
  exit_note?: string | null;
  market?: string;
  price_action?: { text?: string } | null;
  why: string;
  chart: ChartSnap | null;
  index_price: number | null;
}

export interface AssistSettings {
  enabled: boolean;
  slots?: string[];
  slot: string;
  lots: number;
  max_trades: number;
  day_loss: number;
  account_pause: number;
  start_balance: number | null;
}

export interface Guard {
  trades_today: number;
  max_trades: number;
  at_trade_cap: boolean;
  pnl_today: number;
  day_loss: number;
  stop_for_today: boolean;
  open_trades: number;
  start_balance: number | null;
  account_pnl: number;
  equity: number | null;
  account_pause: number;
  pause: boolean;
  needs_start_balance: boolean;
}

export interface AssistRecord {
  key: string;
  kind: "taken" | "skipped" | string;
  slot: string;
  day: string;
  fill_time: number;
  contract?: string | null;
  option_side?: string | null;
  fill_price?: number;
  entry_time?: number;
  exit_time?: number | null;
  exit_price?: number | null;
  net?: number | null;
  stop?: number;
  target?: number;
  sl_set?: boolean;
  tp_set?: boolean;
  trailing?: boolean;
  trail_seen?: { paper_stop: number; moved_at: number }[];
  trails?: { paper_stop: number; moved_at: number; updated_at?: number | null }[];
  paper_fill?: number | null;
  paper_exit_time?: number | null;
  paper_exit_price?: number | null;
  paper_exit_reason?: string | null;
  paper_net?: number | null;
  why?: string;
  chart?: ChartSnap | null;
  reason?: string;
}

export interface RunningRow {
  key: string;
  slot?: string;
  market?: string;
  day: string;
  price_action?: { text?: string } | null;
  after?: { text?: string } | null;
  contract?: string | null;
  option_side?: string | null;
  why?: string;
  chart?: ChartSnap | null;
  paper_entry: number | null;
  real_entry: number | null;
  entry_slip: number | null;
  paper_exit: number | null;
  real_exit: number | null;
  exit_slip: number | null;
  paper_net: number | null;
  real_net: number | null;
  exit_reason?: string | null;
  rule: RuleCheck;
}

export interface RuleCheck {
  entry_delay_s: number | null;
  entered_on_time: boolean;
  sl_tp_set: boolean;
  trails_on_time: boolean;
  no_early_exit: boolean;
  followed: boolean;
}

export interface TrailNotice {
  key: string;
  slot: string;
  market: string;
  contract: string | null;
  paper_stop: number;
  real_stop: number;
  moved_at: number;
  text: string;
}

export interface AssistState {
  settings: AssistSettings;
  guard: Guard;
  open: AssistRecord[];
  records: AssistRecord[];
  running: RunningRow[];
  trails?: TrailNotice[];
  practice?: { tag: string; reads: number; correct: number; accuracy: number | null }[];
  today: string;
}

export interface AssistSignal {
  decided_at_ms: number;
  time: number | null;
  side: string;
  status: string;
  symbol: string | null;
  fill_price: number | null;
  index_price?: number | null;
  order_price?: number | null;
  reason?: string;
  source?: string;
}

export interface AssistExitTrade {
  symbol: string;
  exit_reason: string;
  exit_price?: number;
  entry_price?: number;
  entry_at_ms?: number;
  exit_at_ms?: number;
  net?: number;
  source?: string;
}

export interface AssistSlot {
  slot: string;
  state: string;
  day: string | null;
  source?: string;
  signals?: AssistSignal[];
  trades?: AssistExitTrade[];
}

export interface AssistMemory {
  day: string | null;
  running: boolean;
  entries: Set<string>;
  exits: Set<string>;
}

export interface AssistEvent {
  kind: "entry" | "exit";
  slot: string;
  day: string;
  contract: string;
  decidedAtMs?: number;
  side?: string;
  fillPrice?: number | null;
  time?: number | null;
  indexPrice?: number | null;
  reason?: string;
  exitPrice?: number | null;
  entryPrice?: number | null;
  entryAtMs?: number;
  exitAtMs?: number;
  net?: number;
}

const EXIT_LABELS: Record<string, string> = {
  premium_target: "target", index_target: "target", target: "target",
  premium_stop: "stop", index_stop: "stop", stop: "stop",
  square_off: "square-off",
  reverse: "reversal", signal: "reversal", reversal: "reversal",
};

export function exitLabel(raw: string | null | undefined): string {
  if (!raw) return "exit";
  return EXIT_LABELS[raw] ?? raw.replace(/_/g, " ");
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

export function premiumBracket(price: number, stop = PREMIUM_STOP, target = PREMIUM_TARGET): [number, number] {
  return [round2(price * (1 - stop)), round2(price * (1 + target))];
}

/** Ask snapped to the option tick, half-up, the same rule as the option model. */
export function snapTick(price: number, tick = TICK): number {
  const steps = Math.round(price / tick);
  return Math.max(round2(steps * tick), tick);
}

export function maxLoss(entry: number, stop: number, units: number, charges: number): number {
  return round2(Math.max(0, entry - stop) * units + charges);
}

export function trailsOnTime(record: Pick<AssistRecord, "trailing" | "trail_seen" | "trails">, windowS = TRAIL_WINDOW_S): boolean {
  if (!record.trailing) return true;
  for (const seen of record.trail_seen ?? []) {
    const match = (record.trails ?? []).find((row) => row.paper_stop === seen.paper_stop);
    if (match?.updated_at == null) return false;
    const delay = match.updated_at - seen.moved_at;
    if (delay < 0 || delay > windowS) return false;
  }
  return true;
}

/** The trailing premium stop from the fill the user got, at the best premium since entry. */
export function userTrailStop(fill: number, best: number, stop = 0.1, every = 0.15, by = 0.1): number {
  const base = fill * (1 - stop);
  if (!(best > fill) || !(fill > 0)) return round2(base);
  const steps = Math.floor((best - fill) / (fill * every) + 1e-9);
  return round2(base + steps * by * fill);
}

export function scorePractice(
  signalClose: number,
  upcoming: { high: number; low: number; close: number }[],
  direction: "up" | "down",
  level?: number | null,
): { ready: boolean; directionHit: boolean | null; levelHit: boolean | null } {
  const window = upcoming.slice(0, PRACTICE_BARS);
  if (window.length < PRACTICE_BARS) return { ready: false, directionHit: null, levelHit: null };
  const end = window[window.length - 1]!.close;
  const directionHit = direction === "up" ? end > signalClose : end < signalClose;
  const levelHit = level == null ? null : window.some((bar) => bar.low <= level && level <= bar.high);
  return { ready: true, directionHit, levelHit };
}

export function ruleFollowed(record: Pick<AssistRecord, "fill_time" | "entry_time" | "sl_set" | "tp_set" | "exit_time" | "paper_exit_time" | "trailing" | "trail_seen" | "trails">): RuleCheck {
  const delay = record.entry_time == null ? null : record.entry_time - record.fill_time;
  const onTime = delay != null && delay >= 0 && delay <= ENTRY_WINDOW_S;
  const levels = Boolean(record.sl_set) && (Boolean(record.trailing) || Boolean(record.tp_set));
  const trailsOk = trailsOnTime(record);
  let noEarly = true;
  if (record.exit_time == null) noEarly = true;
  else if (record.paper_exit_time == null) noEarly = false;
  else noEarly = record.exit_time >= record.paper_exit_time;
  return {
    entry_delay_s: delay,
    entered_on_time: onTime,
    sl_tp_set: levels,
    trails_on_time: trailsOk,
    no_early_exit: noEarly,
    followed: Boolean(onTime && levels && trailsOk && noEarly && record.exit_time != null),
  };
}

export function guardFrom(
  records: AssistRecord[],
  settings: Pick<AssistSettings, "max_trades" | "day_loss" | "account_pause" | "start_balance">,
  today: string,
): Guard {
  const taken = records.filter((r) => r.kind === "taken");
  const todayRows = taken.filter((r) => r.day === today);
  const pnlToday = round2(todayRows.reduce((sum, r) => sum + (r.net ?? 0), 0));
  const running = round2(taken.reduce((sum, r) => sum + (r.net ?? 0), 0));
  const start = settings.start_balance;
  return {
    trades_today: todayRows.length,
    max_trades: settings.max_trades,
    at_trade_cap: todayRows.length >= settings.max_trades,
    pnl_today: pnlToday,
    day_loss: settings.day_loss,
    stop_for_today: pnlToday <= -settings.day_loss + 1e-9,
    open_trades: todayRows.filter((r) => r.exit_time == null).length,
    start_balance: start,
    account_pnl: running,
    equity: start == null ? null : round2(start + running),
    account_pause: settings.account_pause,
    pause: start != null && running <= -settings.account_pause + 1e-9,
    needs_start_balance: start == null,
  };
}

export function reminderDue(minuteOfDay: number, openTrades: number, already: boolean): boolean {
  return !already && openTrades > 0 && minuteOfDay >= REMINDER_MINUTE && minuteOfDay < STORY_MINUTE;
}

export function storyDue(minuteOfDay: number): boolean {
  return minuteOfDay >= STORY_MINUTE;
}

export function istMinutes(now: Date): number {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).formatToParts(now);
  const h = Number(parts.find((p) => p.type === "hour")?.value ?? "0");
  const m = Number(parts.find((p) => p.type === "minute")?.value ?? "0");
  return h * 60 + m;
}

export function istDate(now: Date): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit",
  }).format(now);
}

const entryKey = (s: AssistSignal): string => `${s.decided_at_ms}|${s.side}`;
const exitKey = (t: AssistExitTrade): string => `${t.entry_at_ms ?? ""}|${t.exit_at_ms ?? ""}|${t.symbol}`;

/**
 * New live entries and exits since the last poll of this slot.
 * The first poll, a new day, and a slot that is not running only update memory, so a reload does not
 * repeat a signal that already happened. A disabled assist still advances memory.
 */
export function assistEvents(prev: AssistMemory | undefined, status: AssistSlot, enabled: boolean): {
  events: AssistEvent[];
  memory: AssistMemory;
} {
  const entries = (status.signals ?? []).filter(
    (s) => (s.side === "BUY" || s.side === "SELL") && s.status === "filled",
  );
  const trades = status.trades ?? [];
  const memory: AssistMemory = {
    day: status.day,
    running: status.state === "running",
    entries: new Set(entries.map(entryKey)),
    exits: new Set(trades.map(exitKey)),
  };
  const watching = prev !== undefined && prev.running && prev.day === status.day && status.day !== null && enabled;
  if (!watching || status.source === "replay") return { events: [], memory };

  const events: AssistEvent[] = [];
  for (const s of entries) {
    if (prev.entries.has(entryKey(s)) || (s.source ?? "live") !== "live") continue;
    events.push({
      kind: "entry",
      slot: status.slot,
      day: status.day ?? "",
      contract: s.symbol ?? "",
      decidedAtMs: s.decided_at_ms,
      side: s.side,
      fillPrice: s.fill_price,
      time: s.time,
      indexPrice: s.index_price ?? null,
    });
  }
  for (const t of trades) {
    if (prev.exits.has(exitKey(t)) || (t.source ?? "live") !== "live") continue;
    events.push({
      kind: "exit",
      slot: status.slot,
      day: status.day ?? "",
      contract: t.symbol,
      reason: exitLabel(t.exit_reason),
      exitPrice: t.exit_price ?? null,
      entryPrice: t.entry_price ?? null,
      entryAtMs: t.entry_at_ms,
      exitAtMs: t.exit_at_ms,
      net: t.net,
    });
  }
  events.sort((a, b) => (a.kind === b.kind ? 0 : a.kind === "entry" ? -1 : 1));
  return { events, memory };
}

export function paperKey(slot: string, day: string, entryAtMs: number): string {
  return `${slot}:${day}:${Math.trunc(entryAtMs / 1000)}`;
}
