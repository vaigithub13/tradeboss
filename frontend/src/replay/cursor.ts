/** Replay cursor. Stored bars only. The live feed is never started or stopped. */

export const REPLAY_SPEEDS = [1, 2, 5, 10] as const;
export type ReplaySpeed = (typeof REPLAY_SPEEDS)[number];
export type ReplayUnit = "chart" | "1m";

/** Review shortcut. Practice mode must leave this disabled. */
export const NEXT_TRADE_LABEL = "Next trade (review)";

export interface ReplayBar {
  time: number;
  open?: number;
  high?: number;
  low?: number;
  close?: number;
  volume?: number;
}

export interface ReplayTrade {
  id: number;
  entry_time: number;
  exit_time: number;
  stop: number | null;
}

export interface RevealedMarker {
  time: number;
  kind: "entry" | "exit";
  id: number;
}

export interface RevealedStop {
  id: number;
  price: number;
  from: number;
}

export interface Revealed {
  markers: RevealedMarker[];
  stops: RevealedStop[];
  cardId: number | null;
}

export interface LiveHooks {
  start: () => void;
  stop: () => void;
}

export interface ReplayController {
  readonly active: boolean;
  readonly cursor: number | null;
  readonly playing: boolean;
  readonly speed: ReplaySpeed;
  readonly unit: ReplayUnit;
  visible: () => ReplayBar[];
  jumpTo: (target: number) => void;
  play: () => void;
  pause: () => void;
  step: () => void;
  tick: (seconds: number) => void;
  setSpeed: (speed: number) => void;
  setUnit: (unit: ReplayUnit) => void;
  jumpTrade: (trades: readonly { entry_time: number }[]) => void;
  exit: () => void;
}

export function barsUpTo<T extends { time: number }>(bars: readonly T[], cursor: number): T[] {
  return bars.filter((bar) => bar.time <= cursor);
}

export function snapCursor(times: readonly number[], target: number): number | null {
  let found: number | null = null;
  for (const time of times) {
    if (time <= target) found = time;
    else break;
  }
  return found;
}

export function stepBar(times: readonly number[], cursor: number): number {
  for (const time of times) {
    if (time > cursor) return time;
  }
  return times[times.length - 1] ?? cursor;
}

export function advanceBars(times: readonly number[], cursor: number, count: number): number {
  let next = cursor;
  for (let i = 0; i < count; i += 1) {
    const stepped = stepBar(times, next);
    if (stepped === next) return next;
    next = stepped;
  }
  return next;
}

export function nextTradeEntry(trades: readonly { entry_time: number }[], cursor: number): number | null {
  let next: number | null = null;
  for (const trade of trades) {
    if (trade.entry_time > cursor && (next == null || trade.entry_time < next)) next = trade.entry_time;
  }
  return next;
}

export function reveal(trades: readonly ReplayTrade[], cursor: number): Revealed {
  const markers: RevealedMarker[] = [];
  const stops: RevealedStop[] = [];
  let cardId: number | null = null;
  let openId: number | null = null;
  let exitedId: number | null = null;
  for (const trade of trades) {
    if (trade.entry_time > cursor) continue;
    markers.push({ time: trade.entry_time, kind: "entry", id: trade.id });
    if (trade.exit_time <= cursor) {
      markers.push({ time: trade.exit_time, kind: "exit", id: trade.id });
      if (trade.exit_time === cursor) exitedId = trade.id;
    } else {
      if (trade.stop != null) stops.push({ id: trade.id, price: trade.stop, from: trade.entry_time });
      openId = trade.id;
    }
  }
  cardId = openId ?? exitedId;
  return { markers, stops, cardId };
}

export function indicatorTo(visible: readonly { time: number }[]): number | null {
  return visible.length === 0 ? null : visible[visible.length - 1]!.time;
}

const IST_OFFSET_S = 19_800;
const SESSION_OPEN_MIN = 9 * 60 + 15;

/** The next chart-bar open after `cursor`, anchored to 09:15 IST. */
export function nextChartOpen(cursor: number, timeframeSeconds: number): number {
  const local = cursor + IST_OFFSET_S;
  const day = Math.floor(local / 86_400) * 86_400;
  const minute = Math.floor((local % 86_400) / 60);
  const size = Math.max(1, Math.round(timeframeSeconds / 60));
  const k = Math.floor((minute - SESSION_OPEN_MIN) / size);
  const nextMinute = SESSION_OPEN_MIN + (k + 1) * size;
  return day - IST_OFFSET_S + nextMinute * 60;
}

/** IST wall clock to unix seconds. The replay picker is IST, not the browser zone. */
export function istCursor(day: string, time: string): number {
  const [year, month, date] = day.split("-").map(Number);
  const [hour, minute] = time.split(":").map(Number);
  return Math.floor(Date.UTC(year!, (month ?? 1) - 1, date ?? 1, hour ?? 0, minute ?? 0) / 1000 - 5.5 * 3600);
}

/** Aggregate 1-minute bars into chart buckets. The last bucket is only the minutes reached. */
export function chartBars(minutes: readonly ReplayBar[], timeframeSeconds: number, cursor: number): ReplayBar[] {
  const buckets = new Map<number, ReplayBar>();
  for (const bar of minutes) {
    if (bar.time > cursor) break;
    const start = Math.floor(bar.time / timeframeSeconds) * timeframeSeconds;
    const prev = buckets.get(start);
    if (!prev) {
      buckets.set(start, {
        time: start,
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close,
        volume: bar.volume,
      });
      continue;
    }
    prev.high = Math.max(prev.high ?? bar.high ?? 0, bar.high ?? 0);
    prev.low = Math.min(prev.low ?? bar.low ?? 0, bar.low ?? 0);
    prev.close = bar.close;
    prev.volume = (prev.volume ?? 0) + (bar.volume ?? 0);
  }
  return [...buckets.values()];
}

function advanceMinutes(times: readonly number[], cursor: number, count: number): number {
  const last = times[times.length - 1] ?? cursor;
  const target = cursor + count * 60;
  if (target >= last) return last;
  return snapCursor(times, target) ?? cursor;
}

export function createReplay(
  bars: readonly ReplayBar[],
  opts?: {
    live?: LiveHooks;
    strategy?: (visible: readonly ReplayBar[]) => void;
    source?: "stored" | "recording";
    unit?: ReplayUnit;
    timeframeSeconds?: number;
    practice?: boolean;
  },
): ReplayController {
  if (opts?.source === "recording") throw new Error("recorded sessions are later");
  const sorted = [...bars].sort((a, b) => a.time - b.time);
  const times = sorted.map((bar) => bar.time);
  const practice = opts?.practice === true;
  let active = false;
  let cursor: number | null = null;
  let playing = false;
  let speed: ReplaySpeed = 1;
  let unit: ReplayUnit = opts?.unit ?? "chart";

  const notify = (): void => {
    opts?.strategy?.(visible());
  };

  function visible(): ReplayBar[] {
    if (!active) return sorted;
    if (cursor == null) return [];
    if (opts?.timeframeSeconds && sorted.some((bar) => bar.open != null)) {
      return chartBars(sorted, opts.timeframeSeconds, cursor);
    }
    return barsUpTo(sorted, cursor);
  }

  function move(count: number): void {
    if (cursor == null) {
      cursor = times[0] ?? null;
      return;
    }
    cursor = unit === "1m" ? advanceMinutes(times, cursor, count) : advanceBars(times, cursor, count);
  }

  const controller: ReplayController = {
    get active() {
      return active;
    },
    get cursor() {
      return cursor;
    },
    get playing() {
      return playing;
    },
    get speed() {
      return speed;
    },
    get unit() {
      return unit;
    },
    visible,
    jumpTo(target: number) {
      active = true;
      playing = false;
      cursor = snapCursor(times, target);
      notify();
    },
    play() {
      if (!active || cursor == null) return;
      const last = times[times.length - 1];
      if (last != null && cursor >= last) return;
      playing = true;
    },
    pause() {
      playing = false;
    },
    step() {
      if (!active) return;
      playing = false;
      move(1);
      notify();
    },
    tick(seconds: number) {
      if (!playing || cursor == null || seconds <= 0) return;
      const count = Math.floor(speed * seconds);
      if (count <= 0) return;
      move(count);
      notify();
    },
    setSpeed(next: number) {
      if (!(REPLAY_SPEEDS as readonly number[]).includes(next)) {
        throw new Error("speed must be 1, 2, 5, 10");
      }
      speed = next as ReplaySpeed;
    },
    setUnit(next: ReplayUnit) {
      unit = next;
    },
    jumpTrade(trades: readonly { entry_time: number }[]) {
      if (practice) throw new Error(`${NEXT_TRADE_LABEL} is disabled in practice mode`);
      playing = false;
      const from = cursor == null ? Number.NEGATIVE_INFINITY : cursor;
      const next = nextTradeEntry(trades, from);
      if (next == null) return;
      active = true;
      cursor = next;
      notify();
    },
    exit() {
      active = false;
      playing = false;
      cursor = null;
    },
  };
  return controller;
}
