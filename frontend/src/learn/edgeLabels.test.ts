import { describe, expect, it } from "vitest";

import { clearOf, edgeText, stackEdge } from "./edgeLabels";

describe("right-edge line labels", () => {
  it("stacks labels within 14 px instead of overlapping, keeping their order", () => {
    const ys = stackEdge([100, 105, 300, 108]);
    expect(ys[0]).toBe(100);
    expect(ys[1]).toBe(114);
    expect(ys[3]).toBe(128);
    expect(ys[2]).toBe(300);
    const sorted = [...ys].sort((a, b) => a - b);
    for (let i = 1; i < sorted.length; i += 1) expect(sorted[i]! - sorted[i - 1]!).toBeGreaterThanOrEqual(14);
  });

  it("keeps the stack inside the pane", () => {
    const ys = stackEdge([195, 198, 199], 14, 0, 200);
    expect(Math.max(...ys)).toBeLessThanOrEqual(200);
    expect(ys).toEqual([172, 186, 200]);
    expect(stackEdge([-5, 0], 14, 4, 200)).toEqual([4, 18]);
  });

  it("moves a swing pill off a line label, or drops it", () => {
    const label = { x: 400, y: 90, w: 80, h: 12 };
    const pill = clearOf({ x: 420, y: 92, w: 20, h: 13 }, [label], true)!;
    expect(pill.y + pill.h).toBeLessThanOrEqual(90);
    expect(pill.x).toBe(420);
    expect(clearOf({ x: 10, y: 92, w: 20, h: 13 }, [label], true)).toEqual({ x: 10, y: 92, w: 20, h: 13 });
    expect(clearOf({ x: 420, y: 92, w: 20, h: 13 }, [{ x: 0, y: -1000, w: 1000, h: 3000 }], false)).toBeNull();
  });

  it("names the line and its price", () => {
    expect(edgeText("75m CRT low", 25420.4)).toBe("75m CRT low 25,420");
  });
});
