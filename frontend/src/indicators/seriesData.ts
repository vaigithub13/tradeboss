/** Pure mapping from API arrays to chart points (no Lightweight Charts imports, easy to test). */

/**
 * Lightweight Charts has no "gap" for line series: whitespace points are dropped and the line
 * still joins the points on either side. The only way to break a line is a per-point colour: a
 * point's colour is used for the segment that LEAVES it (walkLine strokes the path built so far
 * with the previous style whenever the style changes, then starts the new style at that point).
 * So the LAST point of every run carries this transparent colour, which hides the segment to the
 * first point of the next run. (Verified by pixel scan of the real chart.)
 */
export const BREAK_COLOR = "rgba(0,0,0,0)";

export interface LinePoint {
  time: number;
  /** absent = whitespace */
  value?: number;
  /** per-point override; BREAK_COLOR on the last point of a run hides the segment leaving it */
  color?: string;
}

export interface HistogramPoint extends LinePoint {
  color?: string;
}

const hasValue = (v: number | null | undefined): v is number => v !== null && v !== undefined;

/**
 * Mark the last point of each run: for consecutive valued points i < j, `breakBetween(i, j)` true
 * means the line must not be drawn from i to j. No later point = nothing to hide, so the very last
 * point never gets the colour (a live bar appended later is then joined normally).
 */
function markBreaks(points: LinePoint[], breakBetween: (i: number, j: number) => boolean): LinePoint[] {
  let prev = -1;
  for (let j = 0; j < points.length; j++) {
    const p = points[j];
    if (!p || p.value === undefined) continue;
    const before = points[prev];
    if (prev >= 0 && before && breakBetween(prev, j)) points[prev] = { ...before, color: BREAK_COLOR };
    prev = j;
  }
  return points;
}

/**
 * One line. The line is broken (not joined across) wherever values are missing in the middle and,
 * with `breakBefore`, in front of chosen bars (e.g. the first bar of each day for VWAP).
 */
export function linePoints(
  times: readonly number[],
  values: readonly (number | null)[],
  breakBefore?: (i: number) => boolean,
): LinePoint[] {
  const points = times.map<LinePoint>((time, i) => {
    const v = values[i];
    return hasValue(v) ? { time, value: v } : { time };
  });
  return markBreaks(points, (i, j) => j !== i + 1 || (breakBefore?.(j) ?? false));
}

const IST_OFFSET_S = 19800;
const DAY_S = 86400;
const istDay = (t: number): number => Math.floor((t + IST_OFFSET_S) / DAY_S);

/** `breakBefore` predicate: true on the first bar of each IST calendar day. */
export const firstBarOfIstDay =
  (times: readonly number[]) =>
  (i: number): boolean =>
    i === 0 || istDay(times[i] ?? 0) !== istDay(times[i - 1] ?? 0);

/**
 * Supertrend as two lines so each trend is drawn in its own colour. Pine direction: -1 = uptrend,
 * +1 = downtrend. `up` has a value only on bars where direction is -1, `down` only where it is +1;
 * the lines are broken at every flip so nothing joins separate trend periods.
 */
export function supertrendPoints(
  times: readonly number[],
  line: readonly (number | null)[],
  direction: readonly (number | null)[],
): { up: LinePoint[]; down: LinePoint[] } {
  const side = (wanted: -1 | 1): LinePoint[] => {
    const points = times.map<LinePoint>((time, i) => {
      const v = line[i];
      return hasValue(v) && direction[i] === wanted ? { time, value: v } : { time };
    });
    // a gap between two valued bars means the trend flipped in between: do not join them
    return markBreaks(points, (i, j) => j !== i + 1);
  };
  return { up: side(-1), down: side(1) };
}

export function histogramPoints(
  times: readonly number[],
  values: readonly (number | null)[],
  upColor: string,
  downColor: string,
): HistogramPoint[] {
  return times.map((time, i) => {
    const v = values[i];
    if (!hasValue(v)) return { time };
    return { time, value: v, color: v >= 0 ? upColor : downColor };
  });
}
