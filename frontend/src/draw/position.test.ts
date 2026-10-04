/**
 * Long and short position drawings. See PROJECT_PLAN.md, "Long position and short position".
 *
 * Public API under test (frontend/src/draw/position.ts, not written yet):
 *
 *   defaultPositionSettings() -> account 1000000, risk 1%, rupee risk 10000, lot null,
 *     price mode, green #089981, red #f23645, compact and options off
 *   defaultPositionAnchors(side, entry, timeframe, holidays?) -> entry, target, stop
 *     stop is 1% of the entry, target is 2%, right edge is 15 session bars after the entry bar
 *   movePositionHandle(anchors, "entry" | "target" | "stop" | "right", point) -> new anchors
 *   positionLevels(side, anchors) -> points, percents, ratio (null when risk is not positive)
 *   positionSize(levels, settings, lotSize) -> whole lots from the risk budget; a stored lot wins
 *   priceOf / pointsOf convert a target or stop between a price and a distance from the entry
 *   positionZones(side, entry, target, stop) -> profit band and risk band
 *   positionLabels(levels, size, outcome, compact) -> the text drawn on the tool
 *   positionOutcome(side, anchors, bars, minutes, asOf, quantity) -> open, target, stop, or ambiguous
 *   createPositionDrawing(...) -> a Drawing the object tree, show-on, and replay rules already understand
 *   positionToolLabel(tool) -> "Long position" | "Short position"
 */
import { describe, expect, it } from "vitest";

import { editDrawing, objectTreeRows, shownDrawings, type DrawDoc } from "./model";
import {
  createPositionDrawing,
  defaultPositionAnchors,
  defaultPositionSettings,
  movePositionHandle,
  pointsOf,
  positionLabels,
  positionLevels,
  positionOutcome,
  positionSize,
  positionToolLabel,
  positionZones,
  priceOf,
  type Bar,
  type PositionSettings,
} from "./position";

const T_0915 = 1_790_826_300;
const T_0930 = T_0915 + 900;
const T_0945 = T_0915 + 1_800;
const T_1300 = 1_790_839_800;
const T_1515 = 1_790_847_900;
const MON_1245 = 1_791_184_500;

const entry = { time: T_0915, price: 24_000 };

function bar(time: number, open: number, high: number, low: number, close: number): Bar {
  return { time, open, high, low, close };
}

function sized(lot = 65, budget = 10_000): PositionSettings {
  return { ...defaultPositionSettings(), riskMode: "rupees", riskRupees: budget, lotSize: lot };
}

describe("defaults, handles, and size", () => {
  it("places a long and a short around the click, fifteen session bars wide", () => {
    const long = defaultPositionAnchors("long", entry, "15m");
    expect(long).toEqual([
      entry,
      { time: T_1300, price: 24_480 },
      { time: T_1300, price: 23_760 },
    ]);
    const short = defaultPositionAnchors("short", entry, "15m");
    expect(short[1]).toEqual({ time: T_1300, price: 23_520 });
    expect(short[2]).toEqual({ time: T_1300, price: 24_240 });
    const across = defaultPositionAnchors("long", { time: T_1515, price: 24_000 }, "15m", ["2026-10-02"]);
    expect(across[1]?.time).toBe(MON_1245);
    expect(across[2]?.time).toBe(MON_1245);
  });

  it("moves the entry, one price, or the right edge", () => {
    const anchors = defaultPositionAnchors("long", entry, "15m");
    const target = movePositionHandle(anchors, "target", { time: T_0930, price: 24_100 });
    expect(target[1]).toEqual({ time: T_1300, price: 24_100 });
    expect(target[2]?.price).toBe(23_760);
    const edge = movePositionHandle(anchors, "right", { time: T_0945, price: 1 });
    expect(edge[0]).toEqual(entry);
    expect(edge[1]?.time).toBe(T_0945);
    expect(edge[2]?.time).toBe(T_0945);
    expect(edge[1]?.price).toBe(24_480);
    const moved = movePositionHandle(anchors, "entry", { time: T_0930, price: 24_050 });
    expect(moved[0]).toEqual({ time: T_0930, price: 24_050 });
    expect(moved[1]?.price).toBe(24_480);
  });

  it("turns points into prices and prices into points", () => {
    expect(priceOf("long", 24_000, 200, "target")).toBe(24_200);
    expect(priceOf("long", 24_000, 100, "stop")).toBe(23_900);
    expect(priceOf("short", 24_000, 200, "target")).toBe(23_800);
    expect(priceOf("short", 24_000, 100, "stop")).toBe(24_100);
    expect(pointsOf("long", 24_000, 24_200, "target")).toBe(200);
    expect(pointsOf("short", 24_000, 23_800, "target")).toBe(200);
  });

  it("sizes whole lots from the risk budget and keeps a stored lot", () => {
    const levels = positionLevels("long", [
      entry,
      { time: T_1300, price: 24_200 },
      { time: T_1300, price: 23_900 },
    ]);
    expect(levels.rewardPoints).toBe(200);
    expect(levels.riskPoints).toBe(100);
    expect(levels.ratio).toBe(2);
    const size = positionSize(levels, sized(), 75);
    expect(size).toMatchObject({ riskBudget: 10_000, lotSize: 65, lots: 1, quantity: 65, rewardRupees: 13_000, riskRupees: 6_500 });
    expect(positionSize(levels, sized(65, 5_000), 65).lots).toBe(0);
    expect(positionSize(levels, { ...defaultPositionSettings(), lotSize: null }, 65).lotSize).toBe(65);
    const flat = positionLevels("long", [entry, { time: T_1300, price: 24_200 }, { time: T_1300, price: 24_000 }]);
    expect(flat.ratio).toBeNull();
    expect(positionSize(flat, sized(), 65).lots).toBe(0);
  });

  it("paints profit from entry to target and risk from entry to stop", () => {
    expect(defaultPositionSettings()).toMatchObject({
      accountSize: 1_000_000,
      riskMode: "percent",
      riskPercent: 1,
      riskRupees: 10_000,
      lotSize: null,
      priceMode: "price",
      profitColor: "#089981",
      stopColor: "#f23645",
      compact: false,
      options: false,
    });
    expect(positionZones("long", 24_000, 24_200, 23_900)).toEqual({
      profit: { high: 24_200, low: 24_000 },
      risk: { high: 24_000, low: 23_900 },
    });
    expect(positionZones("short", 24_000, 23_800, 24_100)).toEqual({
      profit: { high: 24_000, low: 23_800 },
      risk: { high: 24_100, low: 24_000 },
    });
  });

  it("writes the full label, the compact label, and a short's signed move", () => {
    const anchors = [
      entry,
      { time: T_1300, price: 24_200 },
      { time: T_1300, price: 23_900 },
    ];
    const levels = positionLevels("long", anchors);
    const size = positionSize(levels, sized(), 65);
    const open = positionOutcome("long", anchors, [bar(T_0915, 24_000, 24_050, 23_980, 24_080)], [], null, size.quantity);
    expect(positionLabels(levels, size, open, false).map((label) => label.text)).toEqual([
      "24200.00 (+200.00, +0.83%) 2.00R ₹13000.00",
      "23900.00 (-100.00, -0.42%) ₹6500.00",
      "open ₹5200.00",
    ]);
    expect(positionLabels(levels, size, open, true).map((label) => label.text)).toEqual(["24200.00 2.00R", "23900.00", "open ₹5200.00"]);
    const shortLevels = positionLevels("short", [
      entry,
      { time: T_1300, price: 23_800 },
      { time: T_1300, price: 24_100 },
    ]);
    expect(positionLabels(shortLevels, size, { status: "pending", endTime: null, exitPrice: null, pnlPoints: null, pnlRupees: null }, false).map((label) => label.text)).toEqual([
      "23800.00 (-200.00, -0.83%) 2.00R ₹13000.00",
      "24100.00 (+100.00, +0.42%) ₹6500.00",
    ]);
  });
});

describe("outcome", () => {
  const anchors = [
    entry,
    { time: T_1300, price: 24_200 },
    { time: T_1300, price: 23_900 },
  ];
  const qty = 65;

  it("follows a long to the target and a short to the stop", () => {
    const running = [
      bar(T_0915, 24_000, 24_050, 23_980, 24_040),
      bar(T_0930, 24_040, 24_100, 24_020, 24_080),
    ];
    const open = positionOutcome("long", anchors, running, [], null, qty);
    expect(open).toMatchObject({ status: "open", endTime: T_0930, exitPrice: 24_080, pnlPoints: 80, pnlRupees: 5_200 });
    const hit = positionOutcome("long", anchors, [...running, bar(T_0945, 24_080, 24_210, 24_050, 24_205)], [], null, qty);
    expect(hit).toMatchObject({ status: "target", endTime: T_0945, exitPrice: 24_200, pnlPoints: 200, pnlRupees: 13_000 });
    const laterStop = positionOutcome(
      "long",
      anchors,
      [...running, bar(T_0945, 24_080, 24_210, 24_050, 24_205), bar(T_0945 + 900, 24_200, 24_200, 23_800, 23_850)],
      [],
      null,
      qty,
    );
    expect(laterStop.status).toBe("target");
    const short = positionOutcome(
      "short",
      [entry, { time: T_1300, price: 23_800 }, { time: T_1300, price: 24_100 }],
      [bar(T_0915, 24_000, 24_120, 23_950, 24_050)],
      [],
      null,
      qty,
    );
    expect(short).toMatchObject({ status: "stop", endTime: T_0915, exitPrice: 24_100, pnlPoints: -100, pnlRupees: -6_500 });
  });

  it("uses the 1-minute bars when one candle reaches both prices", () => {
    const both = [bar(T_0915, 24_000, 24_250, 23_850, 24_100)];
    const minutes = [
      bar(T_0915, 24_000, 24_040, 23_980, 24_020),
      bar(T_0915 + 60, 24_020, 24_210, 24_000, 24_180),
      bar(T_0915 + 120, 24_180, 24_180, 23_850, 23_900),
    ];
    expect(positionOutcome("long", anchors, both, minutes, null, qty)).toMatchObject({
      status: "target",
      endTime: T_0915 + 60,
      exitPrice: 24_200,
    });
    const sameMinute = [bar(T_0915 + 60, 24_020, 24_250, 23_850, 24_000)];
    expect(positionOutcome("long", anchors, both, sameMinute, null, qty)).toMatchObject({
      status: "ambiguous",
      endTime: T_0915 + 60,
      exitPrice: 23_900,
      pnlPoints: -100,
      pnlRupees: -6_500,
    });
    expect(positionOutcome("long", anchors, both, [], null, qty).status).toBe("ambiguous");
    expect(positionLabels(positionLevels("long", anchors), positionSize(positionLevels("long", anchors), sized(), 65), positionOutcome("long", anchors, both, [], null, qty), true).map((label) => label.text)).toContain(
      "ambiguous (stop assumed)",
    );
  });

  it("hides a hit that is still after the replay cursor", () => {
    const bars = [
      bar(T_0915, 24_000, 24_050, 23_980, 24_080),
      bar(T_0930, 24_080, 24_100, 24_020, 24_090),
      bar(T_0945, 24_090, 24_250, 24_080, 24_220),
    ];
    const seen = positionOutcome("long", anchors, bars, [], T_0930, qty);
    expect(seen).toMatchObject({ status: "open", endTime: T_0930, exitPrice: 24_090 });
    expect(positionOutcome("long", anchors, bars, [], T_0915 - 60, qty).status).toBe("pending");
    const drawing = createPositionDrawing("long", entry, "15m", T_0945, "15m");
    expect(shownDrawings([drawing], T_0930, false).map((item) => item.id)).toEqual([]);
    expect(shownDrawings([drawing], T_0945, false)).toHaveLength(1);
    expect(positionToolLabel("long_position")).toBe("Long position");
    expect(positionToolLabel("short_position")).toBe("Short position");
    expect(objectTreeRows([drawing])[0]).toMatchObject({ tool: "long_position", drawnOn: "15m", createdAt: T_0945 });
  });

  it("stops at the right edge and still allows a settings change while locked", () => {
    const planned = [
      entry,
      { time: T_0930, price: 24_200 },
      { time: T_0930, price: 23_900 },
    ];
    const bars = [
      bar(T_0915, 24_000, 24_050, 23_980, 24_040),
      bar(T_0930, 24_040, 24_080, 24_020, 24_060),
      bar(T_0945, 24_060, 24_300, 24_060, 24_280),
    ];
    expect(positionOutcome("long", planned, bars, [], null, qty)).toMatchObject({ status: "open", endTime: T_0930, exitPrice: 24_060 });
    const drawing = createPositionDrawing("long", entry, "15m", T_0915, "15m");
    const doc: DrawDoc = { symbol: "NIFTY50", drawings: [{ ...drawing, locked: true }], lockAll: false, hideAll: false };
    expect(editDrawing(doc, drawing.id, { anchors: planned }).drawings[0]?.anchors).toEqual(drawing.anchors);
    const next = { ...defaultPositionSettings(), compact: true, options: true };
    expect(editDrawing(doc, drawing.id, { position: next }).drawings[0]).toMatchObject({ locked: true, position: next });
  });
});
