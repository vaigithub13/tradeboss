/**
 * Learn-tab structure. Closed bars only. A swing is a pivot (strict, left and right bars)
 * and is labelled only once those right bars exist, so a later bar cannot leak backward.
 *
 * HH / LH compare a swing high with the previous swing high. HL / LL compare swing lows.
 * The first swing of a type is H or L. An equal price is LH or LL.
 *
 * BOS is a close beyond the last swing in the trend direction (the first break sets the trend).
 * CHoCH is the first close beyond the last opposite swing, and it replaces the trend.
 * The badge is Range when the last `rangeSwings` swings contain no BOS. Otherwise it follows
 * the latest BOS in that window.
 */

export interface LearnBar {
  time: number;
  open?: number;
  high: number;
  low: number;
  close: number;
}

export type SwingLabel = "H" | "HH" | "LH" | "L" | "HL" | "LL";

export interface Swing {
  index: number;
  confirmIndex: number;
  time: number;
  price: number;
  kind: "high" | "low";
  label: SwingLabel;
}

export interface BreakEvent {
  index: number;
  time: number;
  kind: "bos" | "choch";
  direction: "up" | "down";
  price: number;
  fromTime: number;
  swingIndex: number;
}

export type TrendBadge = "Uptrend" | "Downtrend" | "Range";

export interface Structure {
  swings: Swing[];
  events: BreakEvent[];
  trend: TrendBadge;
}

export interface StructureBuild {
  swings: Swing[];
  events: BreakEvent[];
  bias: "up" | "down" | null;
  broken: number[];
  next: number;
  left: number;
  right: number;
}

export const DEFAULT_PIVOT = 3;
export const DEFAULT_RANGE_SWINGS = 4;

export function emptyBuild(left = DEFAULT_PIVOT, right = DEFAULT_PIVOT): StructureBuild {
  return { swings: [], events: [], bias: null, broken: [], next: 0, left, right };
}

export function confirmedPivot(bars: readonly LearnBar[], pivot: number, left: number, right: number): { high: boolean; low: boolean } {
  if (pivot < left || pivot + right >= bars.length) return { high: false, low: false };
  const high = bars[pivot]!.high;
  const low = bars[pivot]!.low;
  let isHigh = true;
  let isLow = true;
  for (let j = pivot - left; j <= pivot + right; j += 1) {
    if (j === pivot) continue;
    if (bars[j]!.high >= high) isHigh = false;
    if (bars[j]!.low <= low) isLow = false;
  }
  return { high: isHigh, low: isLow };
}

function labelOf(kind: "high" | "low", price: number, previous: number | null): SwingLabel {
  if (previous == null) return kind === "high" ? "H" : "L";
  if (kind === "high") return price > previous ? "HH" : "LH";
  return price > previous ? "HL" : "LL";
}

function lastOf(swings: readonly Swing[], kind: "high" | "low", before: number): Swing | null {
  for (let i = swings.length - 1; i >= 0; i -= 1) {
    const swing = swings[i]!;
    if (swing.kind === kind && swing.index < before) return swing;
  }
  return null;
}

/** Fold bars[build.next ..] into the build. A shorter series, or a changed earlier bar, starts over. */
export function extendStructure(build: StructureBuild, bars: readonly LearnBar[]): StructureBuild {
  if (build.next > bars.length) return extendStructure(emptyBuild(build.left, build.right), bars);
  const next: StructureBuild = {
    swings: build.swings.slice(),
    events: build.events.slice(),
    bias: build.bias,
    broken: build.broken.slice(),
    next: build.next,
    left: build.left,
    right: build.right,
  };
  const broken = new Set(next.broken);
  for (let i = next.next; i < bars.length; i += 1) {
    if (i >= next.left + next.right) {
      const pivot = i - next.right;
      const hit = confirmedPivot(bars, pivot, next.left, next.right);
      if (hit.high) {
        const prev = lastOf(next.swings, "high", pivot + 1);
        next.swings.push({
          index: pivot,
          confirmIndex: i,
          time: bars[pivot]!.time,
          price: bars[pivot]!.high,
          kind: "high",
          label: labelOf("high", bars[pivot]!.high, prev?.price ?? null),
        });
      }
      if (hit.low) {
        const prev = lastOf(next.swings, "low", pivot + 1);
        next.swings.push({
          index: pivot,
          confirmIndex: i,
          time: bars[pivot]!.time,
          price: bars[pivot]!.low,
          kind: "low",
          label: labelOf("low", bars[pivot]!.low, prev?.price ?? null),
        });
      }
    }
    const close = bars[i]!.close;
    const highSwing = lastOf(next.swings, "high", i);
    const lowSwing = lastOf(next.swings, "low", i);
    const up = highSwing != null && !broken.has(highSwing.index) && close > highSwing.price;
    const down = lowSwing != null && !broken.has(lowSwing.index) && close < lowSwing.price;
    const push = (kind: "bos" | "choch", direction: "up" | "down", swing: Swing) => {
      next.events.push({
        index: i,
        time: bars[i]!.time,
        kind,
        direction,
        price: swing.price,
        fromTime: swing.time,
        swingIndex: swing.index,
      });
      broken.add(swing.index);
      next.bias = direction;
    };
    if (next.bias === "up" && down && lowSwing) push("choch", "down", lowSwing);
    else if (next.bias === "down" && up && highSwing) push("choch", "up", highSwing);
    else if (up && down && highSwing && lowSwing) {
      const upDist = close - highSwing.price;
      const downDist = lowSwing.price - close;
      if (upDist >= downDist) push("bos", "up", highSwing);
      else push("bos", "down", lowSwing);
    } else if (up && next.bias !== "down" && highSwing) push("bos", "up", highSwing);
    else if (down && next.bias !== "up" && lowSwing) push("bos", "down", lowSwing);
    next.next = i + 1;
  }
  next.broken = [...broken];
  return next;
}

export function trendOf(build: StructureBuild, rangeSwings = DEFAULT_RANGE_SWINGS): TrendBadge {
  const recent = build.swings.slice(-rangeSwings);
  if (recent.length === 0) return "Range";
  const since = recent[0]!.confirmIndex;
  const bos = build.events.filter((event) => event.kind === "bos" && event.index >= since);
  if (bos.length === 0) return "Range";
  return bos[bos.length - 1]!.direction === "up" ? "Uptrend" : "Downtrend";
}

export function readStructure(
  bars: readonly LearnBar[],
  opts?: { left?: number; right?: number; rangeSwings?: number },
): Structure {
  const build = extendStructure(emptyBuild(opts?.left ?? DEFAULT_PIVOT, opts?.right ?? DEFAULT_PIVOT), bars);
  return { swings: build.swings, events: build.events, trend: trendOf(build, opts?.rangeSwings ?? DEFAULT_RANGE_SWINGS) };
}

/** Bars the structure may see. Replay drops anything after the cursor. The last bar is still forming. */
export function closedBars<T extends { time: number }>(bars: readonly T[], cursor: number | null): T[] {
  const shown = cursor == null ? bars : bars.filter((bar) => bar.time <= cursor);
  if (shown.length <= 1) return [];
  return shown.slice(0, -1);
}

export function shownBars<T extends { time: number }>(bars: readonly T[], cursor: number | null): T[] {
  if (cursor == null) return [...bars];
  return bars.filter((bar) => bar.time <= cursor);
}
