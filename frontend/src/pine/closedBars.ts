/**
 * Closed-bar bus: ChartView emits AFTER series.update (via queueMicrotask), so the live
 * candle path never waits on a Pine script. Scripts must never be sent on the live WS view.
 */

export type ChartScopeId = "nifty" | "sensex";

export interface ClosedBarEvent {
  scope: ChartScopeId;
  symbol: string;
  timeframe: string;
  /** unix seconds of the bar that just closed */
  closedTime: number;
  /** all candles currently on the chart (including the new forming tip, if any) */
  candles: readonly {
    time: number;
    open: number;
    high: number;
    low: number;
    close: number;
    volume: number;
  }[];
  /** true when this is the first history load / full setData (not a live append) */
  history: boolean;
}

type Listener = (ev: ClosedBarEvent) => void;

const listeners = new Set<Listener>();

export function subscribeClosedBars(fn: Listener): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

/** Fire after the chart has already applied series.update / setData. */
export function emitClosedBar(ev: ClosedBarEvent): void {
  for (const fn of listeners) {
    try {
      fn(ev);
    } catch (err) {
      console.error("[pine] closed-bar listener", err);
    }
  }
}

/**
 * Schedule emit on a microtask so it always runs after the current synchronous
 * series.update returns. Prefer this from ChartView.
 */
export function scheduleClosedBar(ev: ClosedBarEvent): void {
  queueMicrotask(() => emitClosedBar(ev));
}

/** Detect a live append: previous tip closed when a newer bar appears. */
export function closedTimeOnAppend(
  prev: readonly { time: number }[],
  next: readonly { time: number }[],
): number | null {
  if (next.length !== prev.length + 1) return null;
  const was = prev[prev.length - 1];
  const tip = next[next.length - 1];
  if (!was || !tip || tip.time <= was.time) return null;
  return was.time;
}
