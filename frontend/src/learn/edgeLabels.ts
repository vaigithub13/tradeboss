/** Right-edge labels for horizontal lines, stacked so they never overlap. */

import { rectsOverlap, type Rect } from "./chartLook";

export const EDGE_GAP = 14;

/**
 * Nudge right-edge labels apart. Each label keeps its order by price and sits
 * at least `gap` px from the next. The column is kept inside [top, bottom].
 * Returns the new y for each input, in the input order.
 */
export function stackEdge(ys: readonly number[], gap = EDGE_GAP, top = 0, bottom = Number.POSITIVE_INFINITY): number[] {
  const order = ys.map((y, index) => ({ y, index })).sort((a, b) => a.y - b.y || a.index - b.index);
  const placed = order.map((item) => item.y);
  for (let i = 1; i < placed.length; i += 1) placed[i] = Math.max(placed[i]!, placed[i - 1]! + gap);
  const last = placed.length - 1;
  if (last >= 0 && placed[last]! > bottom) {
    placed[last] = bottom;
    for (let i = last - 1; i >= 0; i -= 1) placed[i] = Math.min(placed[i]!, placed[i + 1]! - gap);
  }
  if (placed.length && placed[0]! < top) {
    placed[0] = top;
    for (let i = 1; i < placed.length; i += 1) placed[i] = Math.max(placed[i]!, placed[i - 1]! + gap);
  }
  const out = new Array<number>(ys.length);
  order.forEach((item, k) => { out[item.index] = placed[k]!; });
  return out;
}

/** Move a candle pill up (above) or down until it clears every reserved rect. Null if it cannot. */
export function clearOf(rect: Rect, reserved: readonly Rect[], above: boolean, tries = 6): Rect | null {
  let probe = { ...rect };
  for (let step = 0; step <= tries; step += 1) {
    if (!reserved.some((other) => rectsOverlap(probe, other))) return probe;
    probe = { ...probe, y: probe.y + (above ? -EDGE_GAP : EDGE_GAP) };
  }
  return null;
}

/** Text for a level line at the right edge: its name and its price. */
export function edgeText(label: string, price: number): string {
  return `${label} ${Math.round(price).toLocaleString("en-IN")}`;
}
