import { describe, expect, it } from "vitest";

import {
  BREAK_COLOR,
  firstBarOfIstDay,
  histogramPoints,
  linePoints,
  supertrendPoints,
  type LinePoint,
} from "./seriesData";

const valued = (pts: LinePoint[]): number[] => pts.filter((p) => p.value !== undefined).map((p) => p.time);

describe("linePoints", () => {
  it("keeps every time; null becomes whitespace and leading gaps need no break", () => {
    expect(linePoints([1, 2, 3], [null, 5, 6])).toEqual([{ time: 1 }, { time: 2, value: 5 }, { time: 3, value: 6 }]);
  });

  it("keeps zero as a real value", () => {
    expect(linePoints([1], [0])).toEqual([{ time: 1, value: 0 }]);
  });

  it("breaks the line over a gap in the middle (the last point before it hides the segment leaving it)", () => {
    const pts = linePoints([1, 2, 3, 4], [1, null, 3, 4]);
    expect(pts[0]).toEqual({ time: 1, value: 1, color: BREAK_COLOR });
    expect(pts[2]).toEqual({ time: 3, value: 3 });
    expect(pts[3]).toEqual({ time: 4, value: 4 });
  });

  it("breaks in front of a chosen bar (breakBefore): the bar before it carries the colour", () => {
    const pts = linePoints([1, 2, 3, 4], [1, 2, 3, 4], (i) => i === 0 || i === 2);
    expect(pts.map((p) => p.color)).toEqual([undefined, BREAK_COLOR, undefined, undefined]);
  });

  it("never colours the very last point (a live bar appended later joins normally)", () => {
    const pts = linePoints([1, 2], [1, 2], (i) => i === 1);
    expect(pts.map((p) => p.color)).toEqual([BREAK_COLOR, undefined]);
    expect(linePoints([1, 2, 3], [1, 2, 3]).map((p) => p.color)).toEqual([undefined, undefined, undefined]);
  });
});

describe("firstBarOfIstDay", () => {
  // 2024-01-01 09:15 IST = 03:45 UTC; 2024-01-02 09:15 IST is one day later
  const d1 = Date.UTC(2024, 0, 1, 3, 45) / 1000;
  const times = [d1, d1 + 900, d1 + 86400, d1 + 86400 + 900];

  it("is true on the first bar of each IST day only", () => {
    const first = firstBarOfIstDay(times);
    expect([0, 1, 2, 3].map(first)).toEqual([true, false, true, false]);
  });

  it("uses the IST calendar, not UTC (a bar at 00:15 IST is already the next day)", () => {
    const late = Date.UTC(2024, 0, 1, 18, 40) / 1000; // 00:10 IST on Jan 2
    const first = firstBarOfIstDay([d1, late]);
    expect(first(1)).toBe(true);
  });

  it("VWAP: each day starts a fresh segment", () => {
    const pts = linePoints(times, [1, 2, 3, 4], firstBarOfIstDay(times));
    // bar 1 is the last of day 1 -> hides the overnight segment to bar 2
    expect(pts.map((p) => p.color)).toEqual([undefined, BREAK_COLOR, undefined, undefined]);
  });
});

describe("supertrendPoints (direction -1 = up, +1 = down)", () => {
  it("splits into an up line and a down line", () => {
    const { up, down } = supertrendPoints([1, 2, 3, 4], [10, 11, 12, 13], [1, -1, -1, 1]);
    expect(valued(down)).toEqual([1, 4]);
    expect(valued(up)).toEqual([2, 3]);
  });

  it("up has NO value on any bar where direction is +1, and down none where it is -1", () => {
    // many flips, including 1-bar runs and a warm-up gap
    const direction = [null, null, 1, 1, -1, 1, -1, -1, -1, 1, 1, -1];
    const times = direction.map((_, i) => 100 + i * 300);
    const line = direction.map((d, i) => (d === null ? null : 50 + i));
    const { up, down } = supertrendPoints(times, line, direction);

    direction.forEach((d, i) => {
      if (d === 1) expect(up[i]?.value).toBeUndefined();
      if (d === -1) expect(down[i]?.value).toBeUndefined();
      if (d === -1) expect(up[i]?.value).toBe(50 + i);
      if (d === 1) expect(down[i]?.value).toBe(50 + i);
      if (d === null) {
        expect(up[i]?.value).toBeUndefined();
        expect(down[i]?.value).toBeUndefined();
      }
    });
    // output covers every bar once, in order
    expect(up.map((p) => p.time)).toEqual(times);
    expect(down.map((p) => p.time)).toEqual(times);
  });

  it("ends every run that is followed by another with the transparent break colour (no diagonal joins)", () => {
    //            down down  up   up  down  up
    const dir = [1, 1, -1, -1, 1, -1];
    const times = dir.map((_, i) => i + 1);
    const { up, down } = supertrendPoints(times, [10, 11, 12, 13, 14, 15], dir);
    // up: run [2,3] then run [5]; down: run [0,1] then run [4]
    expect(up.map((p) => p.color)).toEqual([undefined, undefined, undefined, BREAK_COLOR, undefined, undefined]);
    expect(down.map((p) => p.color)).toEqual([undefined, BREAK_COLOR, undefined, undefined, undefined, undefined]);
    // inside a run the colour is left alone, so the segment is drawn in the series colour
    expect(up[2]?.color).toBeUndefined();
  });

  it("has no points while the indicator is warming up", () => {
    const { up, down } = supertrendPoints([1, 2], [null, 5], [null, 1]);
    expect(up).toEqual([{ time: 1 }, { time: 2 }]);
    expect(down).toEqual([{ time: 1 }, { time: 2, value: 5 }]);
  });
});

describe("histogramPoints", () => {
  it("colours by sign (zero counts as positive) and leaves gaps for null", () => {
    expect(histogramPoints([1, 2, 3, 4], [null, 2, -1, 0], "#0f0", "#f00")).toEqual([
      { time: 1 },
      { time: 2, value: 2, color: "#0f0" },
      { time: 3, value: -1, color: "#f00" },
      { time: 4, value: 0, color: "#0f0" },
    ]);
  });
});
