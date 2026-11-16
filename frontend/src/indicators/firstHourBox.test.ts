import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import {
  computeFirstHourBox,
  firstHourBoxVisibleOn,
  formatBoxAxisLabel,
  formatInrPrice,
  nextThirtyCloseAfter,
  uniqueAscendingMarkers,
  type FhbCandle,
  type FhbKind,
} from "./firstHourBox";

const FIXTURE_DIR = path.resolve(__dirname, "../../../tests/fixtures/first_hour_box");

interface Fixture {
  name: string;
  asOf: number | null;
  maxWidth?: number;
  candles: FhbCandle[];
  expected: {
    boxes: {
      day: string;
      high: number;
      low: number;
      mid: number;
      width: number;
      tooWide: boolean;
      forming: boolean;
      markerKinds: FhbKind[];
    }[];
  };
}

function loadFixtures(): Fixture[] {
  return readdirSync(FIXTURE_DIR)
    .filter((f) => f.endsWith(".json"))
    .sort()
    .map((f) => JSON.parse(readFileSync(path.join(FIXTURE_DIR, f), "utf8")) as Fixture);
}

describe("computeFirstHourBox fixtures", () => {
  for (const fx of loadFixtures()) {
    it(fx.name, () => {
      const result = computeFirstHourBox(fx.candles, { asOf: fx.asOf, maxWidth: fx.maxWidth ?? 150 });
      expect(result.boxes).toHaveLength(fx.expected.boxes.length);
      for (let i = 0; i < fx.expected.boxes.length; i++) {
        const got = result.boxes[i]!;
        const exp = fx.expected.boxes[i]!;
        expect(got.day).toBe(exp.day);
        expect(got.high).toBe(exp.high);
        expect(got.low).toBe(exp.low);
        expect(got.mid).toBe(exp.mid);
        expect(got.width).toBe(exp.width);
        expect(got.tooWide).toBe(exp.tooWide);
        expect(got.forming).toBe(exp.forming);
        expect(got.markers.map((m) => m.kind)).toEqual(exp.markerKinds);
      }
      const times = result.markers.map((m) => m.time);
      expect(times).toEqual([...times].sort((a, b) => a - b));
      expect(new Set(times).size).toBe(times.length);
    });
  }
});

describe("firstHourBox helpers", () => {
  it("formats Indian prices and axis labels", () => {
    expect(formatInrPrice(24350)).toBe("24,350.00");
    expect(formatBoxAxisLabel("BOX H", 24350)).toBe("BOX H 24,350");
    expect(formatBoxAxisLabel("BOX L", 24210.5)).toBe("BOX L 24,210.50");
  });

  it("hides on 1h/1D/1W and shows on intraday ≤30m", () => {
    expect(firstHourBoxVisibleOn("5m")).toBe(true);
    expect(firstHourBoxVisibleOn("1h")).toBe(false);
  });

  it("nextThirtyCloseAfter finds the next 10:45…15:15 close", () => {
    // 2026-11-17 10:00 IST → next close 10:45
    expect(nextThirtyCloseAfter(1794889800)).toBe(1794892500);
  });

  it("uniqueAscendingMarkers keeps last on duplicate time", () => {
    const out = uniqueAscendingMarkers([
      { time: 2, kind: "PROBE", side: "above", label: "a", color: "#f59e0b" },
      { time: 1, kind: "BREAK", side: "below", label: "b", color: "#3b82f6" },
      { time: 2, kind: "FAILED", side: "below", label: "c", color: "#a855f7" },
    ]);
    expect(out.map((m) => m.time)).toEqual([1, 2]);
    expect(out[1]?.kind).toBe("FAILED");
  });
});
