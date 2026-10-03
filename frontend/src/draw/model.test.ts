/**
 * Drawing model spec. Written before the chart toolbar.
 * Implementation is not in this slice. See PROJECT_PLAN.md, "Drawings and fair value gaps".
 *
 * Public API under test (frontend/src/draw/model.ts, not written yet):
 *
 *   mapAnchor(anchor, timeframe) -> same price, time moved to the bar that contains it
 *   whitespaceTimes(lastBarTime, timeframe, count) -> future bar starts, after the last candle
 *   snapPrice(price, bar) -> nearest open/high/low/close; a tie keeps the earlier one
 *   shownDrawings(drawings, cursor, hideAll) -> hides createdAt after the cursor, and everything when hideAll
 *   createHistory / commit / undo / redo
 *   editDrawing refuses changes while lockAll is set
 *   removeDrawing, translate, setAnchor
 *   fibPrices, measure
 */
import { describe, expect, it } from "vitest";

import {
  createHistory,
  editDrawing,
  fibPrices,
  mapAnchor,
  measure,
  removeDrawing,
  setAnchor,
  shownDrawings,
  snapPrice,
  translate,
  whitespaceTimes,
  type Drawing,
  type DrawDoc,
} from "./model";

const T_0915 = 1_790_826_300;
const T_1015 = 1_790_829_900;
const T_1020 = 1_790_830_200;
const T_1245 = 1_790_838_900;
const T_1247 = 1_790_839_020;
const T_1300 = 1_790_839_800;

const bar = { open: 22400, high: 22420, low: 22390, close: 22416 };

function drawing(id: string, createdAt: number, time = T_1247): Drawing {
  return {
    id,
    tool: "trend",
    anchors: [
      { time, price: 100 },
      { time: time + 60, price: 110 },
    ],
    createdAt,
    text: "",
    style: {
      color: "#2962ff",
      width: 1,
      lineStyle: "solid",
      extendLeft: false,
      extendRight: false,
      fill: null,
    },
  };
}

describe("anchors stay in time and price", () => {
  it("maps a 12:47 anchor onto the 15-minute bar that contains it, and does not rewrite the stored time", () => {
    const stored = { time: T_1247, price: 22416 };
    expect(mapAnchor(stored, "15m")).toEqual({ time: T_1245, price: 22416 });
    expect(mapAnchor(stored, "1h")).toEqual({ time: 1_790_837_100, price: 22416 });
    expect(mapAnchor({ time: T_1020, price: 1 }, "1h").time).toBe(T_1015);
    expect(mapAnchor({ time: T_1020, price: 1 }, "1D").time).toBe(T_0915);
    expect(stored.time).toBe(T_1247);
  });

  it("places an anchor in the empty area after the last candle", () => {
    const [next, after] = whitespaceTimes(T_1245, "15m", 2);
    expect(next).toBe(T_1300);
    expect(after).toBe(T_1300 + 900);
    expect(next).toBeGreaterThan(T_1245);
    const stored = { time: next, price: 22400 };
    expect(mapAnchor(stored, "15m").time).toBe(next);
    expect(stored.time).toBe(next);
  });

  it("snaps to the nearest open, high, low, or close", () => {
    expect(snapPrice(22410, bar)).toBe(22416);
    expect(snapPrice(22410, { open: 22400, high: 22420, low: 22390, close: 22450 })).toBe(22400);
  });
});

describe("undo, lock, and replay", () => {
  const empty: DrawDoc = { symbol: "NIFTY50", drawings: [], lockAll: false, hideAll: false };

  it("undoes and redoes, and a new edit drops the redo branch", () => {
    const history = createHistory(empty);
    const one: DrawDoc = { ...empty, drawings: [drawing("a", T_1245)] };
    const two: DrawDoc = { ...empty, drawings: [drawing("a", T_1245), drawing("b", T_1245)] };
    history.commit(one);
    history.commit(two);
    expect(history.undo().drawings.map((item) => item.id)).toEqual(["a"]);
    expect(history.redo().drawings.map((item) => item.id)).toEqual(["a", "b"]);
    history.undo();
    history.commit({ ...empty, drawings: [drawing("c", T_1245)] });
    expect(history.redo().drawings.map((item) => item.id)).toEqual(["c"]);
    expect(history.undo()).toEqual(empty);
  });

  it("refuses a move, a handle edit, and a delete while everything is locked", () => {
    const doc: DrawDoc = { ...empty, drawings: [drawing("a", T_1245)], lockAll: true };
    expect(editDrawing(doc, "a", { anchors: [{ time: T_1300, price: 1 }] })).toBe(doc);
    expect(removeDrawing(doc, "a")).toBe(doc);
  });

  it("moves a drawing by time and price, and moves one handle", () => {
    const line = drawing("a", T_1245);
    const moved = translate(line, 900, 5);
    expect(moved.anchors[0]).toEqual({ time: T_1247 + 900, price: 105 });
    expect(setAnchor(line, 1, { time: T_1300, price: 90 }).anchors[1]).toEqual({ time: T_1300, price: 90 });
    expect(line.anchors[1]?.price).toBe(110);
  });

  it("hides a drawing created after the replay cursor, and hides every drawing when hide all is on", () => {
    const early = drawing("early", T_1245);
    const later = drawing("later", T_1300);
    expect(shownDrawings([early, later], T_1245, false).map((item) => item.id)).toEqual(["early"]);
    expect(shownDrawings([early, later], null, false)).toHaveLength(2);
    expect(shownDrawings([early], null, true)).toEqual([]);
  });
});

describe("fibonacci and measure", () => {
  it("prices the retracement from the second anchor back to the first", () => {
    const levels = fibPrices({ time: T_1245, price: 100 }, { time: T_1300, price: 80 });
    expect(levels.map((level) => level.ratio)).toEqual([0, 0.236, 0.382, 0.5, 0.618, 0.786, 1]);
    expect(levels.find((level) => level.ratio === 0)?.price).toBe(80);
    expect(levels.find((level) => level.ratio === 0.5)?.price).toBe(90);
    expect(levels.find((level) => level.ratio === 1)?.price).toBe(100);
  });

  it("measures price, percent, seconds, and chart bars", () => {
    expect(
      measure({ time: T_1245, price: 100 }, { time: T_1300, price: 110 }, "15m"),
    ).toEqual({ price: 10, percent: 10, seconds: 900, bars: 1 });
  });
});
