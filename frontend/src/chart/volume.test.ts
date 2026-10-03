import { describe, expect, it } from "vitest";

import type { Candle } from "../api/client";
import { hasVolume } from "./volume";

const c = (volume: number): Candle => ({
  time: 1,
  open: 1,
  high: 1,
  low: 1,
  close: 1,
  volume,
  oi: null,
});

describe("hasVolume", () => {
  it("is false for an empty range", () => {
    expect(hasVolume([])).toBe(false);
  });

  it("is false when ALL volume is 0 (index data)", () => {
    expect(hasVolume([c(0), c(0), c(0)])).toBe(false);
  });

  it("is true when any candle has volume", () => {
    expect(hasVolume([c(0), c(0), c(1500)])).toBe(true);
  });
});
