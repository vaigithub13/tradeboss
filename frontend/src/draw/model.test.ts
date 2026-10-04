/**
 * Drawing model. See PROJECT_PLAN.md, "Drawings and fair value gaps".
 *
 * Public API under test (frontend/src/draw/model.ts, not written yet):
 *
 *   mapAnchor(anchor, timeframe) -> same price, time moved to the bar that contains it
 *   whitespaceTimes(lastBarTime, timeframe, count) -> future bar starts, after the last candle
 *   snapPrice(price, bar) -> nearest open/high/low/close; a tie keeps the earlier one
 *   shownDrawings(drawings, cursor, hideAll, timeframe?) -> hides knownAt after the cursor,
 *     everything when hideAll, a hidden drawing, and a drawing whose showOn skips the timeframe
 *   stampKnownAt(drawing, cursor, lastBarTime) -> replay stamps the cursor, otherwise the last bar
 *   createHistory / commit / undo / redo
 *   editDrawing refuses geometry while lockAll or the drawing is locked; hide, lock, and showOn still apply
 *   removeDrawing, translate, setAnchor
 *   fibPrices, measure
 *   objectTreeRows, labelsForWidth, hitsSegment, hitsBox, zoomLogical, visibleOnTimeframe
 */
import { describe, expect, it } from "vitest";

import {
  createHistory,
  editDrawing,
  fibPrices,
  hitsBox,
  hitsSegment,
  labelsForWidth,
  mapAnchor,
  measure,
  anchorLogical,
  barsBetween,
  clipSegment,
  historyForZoom,
  objectTreeRows,
  removeDrawing,
  setAnchor,
  shownDrawings,
  snapPrice,
  stampKnownAt,
  translate,
  visibleOnTimeframe,
  whitespaceTimes,
  zoomLogical,
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

function drawing(id: string, knownAt: number, time = T_1247): Drawing {
  return {
    id,
    tool: "trend",
    anchors: [
      { time, price: 100 },
      { time: time + 60, price: 110 },
    ],
    knownAt,
    drawnOn: "15m",
    showOn: null,
    hidden: false,
    locked: false,
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
    const times = whitespaceTimes(T_1245, "15m", 2);
    const next = times[0];
    const after = times[1];
    if (next == null || after == null) throw new Error("expected two slots");
    expect(next).toBe(T_1300);
    expect(after).toBe(T_1300 + 900);
    expect(next).toBeGreaterThan(T_1245);
    const stored = { time: next, price: 22400 };
    expect(mapAnchor(stored, "15m").time).toBe(next);
    expect(stored.time).toBe(next);
  });

  it("skips the night, the weekend and a holiday, and lands on that candle when it arrives", () => {
    const last = 1_790_847_900; // Thu 1 Oct 2026 15:15 IST
    const mondayOpen = 1_791_171_900; // Mon 5 Oct 2026 09:15, after the 2 Oct holiday
    const slot = whitespaceTimes(last, "15m", 1, ["2026-10-02"])[0];
    if (slot == null) throw new Error("expected a slot");
    expect(slot).toBe(mondayOpen);
    expect(slot).not.toBe(1_790_848_800); // 15:30 is the close, not a bar
    expect(slot).not.toBe(1_790_912_700); // Fri 2 Oct 09:15 is a holiday
    const anchor = { time: slot, price: 22400 };
    expect(mapAnchor(anchor, "15m").time).toBe(mondayOpen);
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
    expect(history.undo().drawings.map((item) => item.id)).toEqual(["a"]);
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

  it("keeps a drawing made during replay and hides a live drawing from a later date", () => {
    const during = stampKnownAt(drawing("during", 0), T_1245, T_1300);
    expect(during.knownAt).toBe(T_1245);
    const laterLive = drawing("later", T_1300);
    expect(shownDrawings([during, laterLive], T_1245, false).map((item) => item.id)).toEqual(["during"]);
    expect(shownDrawings([during, laterLive], null, false)).toHaveLength(2);
    expect(shownDrawings([during], null, true)).toEqual([]);
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

describe("object tree", () => {
  const empty: DrawDoc = { symbol: "NIFTY50", drawings: [], lockAll: false, hideAll: false };

  it("lists every drawing with its type, the timeframe it was drawn on, and the time it was created", () => {
    const fib = { ...drawing("fib", T_1247), tool: "fib" as const, drawnOn: "15m" };
    const dailyOnly = { ...drawing("daily", T_1300), drawnOn: "1D", showOn: ["1D"] as const, hidden: true };
    const rows = objectTreeRows([fib, dailyOnly]);
    expect(rows).toEqual([
      { id: "fib", tool: "fib", drawnOn: "15m", createdAt: T_1247, hidden: false, locked: false },
      { id: "daily", tool: "trend", drawnOn: "1D", createdAt: T_1300, hidden: true, locked: false },
    ]);
    expect(shownDrawings([fib, dailyOnly], null, false, "1D").map((item) => item.id)).toEqual(["fib"]);
  });

  it("selects, hides, locks, deletes, and zooms from the list without a screen hit", () => {
    const fib = { ...drawing("fib", T_1247), tool: "fib" as const };
    const doc: DrawDoc = { ...empty, drawings: [fib] };
    const hidden = editDrawing(doc, "fib", { hidden: true });
    expect(hidden.drawings[0]?.hidden).toBe(true);
    expect(shownDrawings(hidden.drawings, null, false, "1D")).toEqual([]);
    expect(objectTreeRows(hidden.drawings)[0]?.id).toBe("fib");

    const locked = editDrawing(hidden, "fib", { locked: true });
    expect(editDrawing(locked, "fib", { anchors: [{ time: T_1300, price: 1 }] })).toBe(locked);
    expect(removeDrawing(locked, "fib")).toBe(locked);
    const unlocked = editDrawing(locked, "fib", { locked: false });
    expect(removeDrawing(unlocked, "fib").drawings).toEqual([]);

    const day = T_0915;
    const range = zoomLogical([day, day + 86_400], T_1245, T_1247);
    expect(range).not.toBeNull();
    expect(range && range.from).toBeLessThanOrEqual(0);
    expect(range && range.to).toBeGreaterThan(0);
    const future = zoomLogical([day, day + 86_400], day + 86_400 * 3, day + 86_400 * 4);
    expect(future && future.to).toBeGreaterThan(2);
  });
});

describe("timeframe visibility", () => {
  it("shows a drawing on every timeframe until its list says otherwise", () => {
    const line = drawing("a", T_1245);
    expect(line.showOn).toBeNull();
    expect(visibleOnTimeframe(line, "1D")).toBe(true);
    expect(visibleOnTimeframe(line, "15m")).toBe(true);
    expect(shownDrawings([line], null, false, "1D")).toHaveLength(1);

    const intraday = { ...line, showOn: ["15m"] };
    expect(visibleOnTimeframe(intraday, "1D")).toBe(false);
    expect(visibleOnTimeframe(intraday, "15m")).toBe(true);
    expect(shownDrawings([intraday], null, false, "1D")).toEqual([]);
    expect(shownDrawings([intraday], null, false, "15m")).toHaveLength(1);
  });
});

describe("narrow drawings", () => {
  const fibLabels = [0, 4, 8, 30, 34, 80].map((y) => ({ y, text: String(y) }));

  it("hides text when the drawing is narrower than 10px and keeps only Fibonacci labels that fit", () => {
    expect(labelsForWidth("fib", 0, fibLabels)).toEqual([]);
    expect(labelsForWidth("fib", 9, fibLabels)).toEqual([]);
    expect(labelsForWidth("measure", 9, [{ y: 0, text: "10" }])).toEqual([]);
    expect(labelsForWidth("measure", 40, [{ y: 0, text: "10" }])).toEqual([{ y: 0, text: "10" }]);
    expect(labelsForWidth("fib", 40, fibLabels)).toEqual([
      { y: 0, text: "0" },
      { y: 30, text: "30" },
      { y: 80, text: "80" },
    ]);
  });

  it("hits a collapsed drawing within a few pixels and misses a point farther away", () => {
    const collapsed = { x1: 100, y1: 100, x2: 100, y2: 100 };
    expect(hitsSegment(103, 100, collapsed)).toBe(true);
    expect(hitsSegment(100, 104, collapsed)).toBe(true);
    expect(hitsSegment(120, 100, collapsed)).toBe(false);
    expect(hitsSegment(50, 40, { x1: 0, y1: 40, x2: 100, y2: 40 })).toBe(true);
    expect(hitsBox(100, 50, 100, 50, 0, 0)).toBe(true);
    expect(hitsBox(120, 50, 100, 50, 0, 0)).toBe(false);
  });
});

const IST_OFFSET = 19_800;
const DAY_SECONDS = 86_400;
const SESSION_OPEN = 9 * 60 + 15;
const SESSION_CLOSE = 15 * 60 + 30;
const MINUTE_BARS = SESSION_CLOSE - SESSION_OPEN;

function dayIndex(year: number, month: number, date: number): number {
  const unix = Date.UTC(year, month - 1, date, 3, 45, 0) / 1000;
  return Math.floor((unix + IST_OFFSET) / DAY_SECONDS);
}

function dateOf(day: number): string {
  const noon = new Date((day * DAY_SECONDS - IST_OFFSET + 12 * 3600) * 1000);
  const month = String(noon.getUTCMonth() + 1).padStart(2, "0");
  const date = String(noon.getUTCDate()).padStart(2, "0");
  return `${noon.getUTCFullYear()}-${month}-${date}`;
}

function weekendDay(day: number): boolean {
  return (day + 3) % 7 >= 5;
}

/** 1m bar starts, built without barsBetween, so the calendar count can be checked against it. */
function minuteTape(startDay: number, sessions: number, holidays: readonly string[] = []): number[] {
  const closed = new Set(holidays);
  const out: number[] = [];
  let day = startDay;
  let found = 0;
  while (found < sessions && day < startDay + 80) {
    if (!weekendDay(day) && !closed.has(dateOf(day))) {
      for (let minute = SESSION_OPEN; minute < SESSION_CLOSE; minute += 1) {
        out.push(day * DAY_SECONDS - IST_OFFSET + minute * 60);
      }
      found += 1;
    }
    day += 1;
  }
  return out;
}

describe("an anchor before the loaded bars still has a place on the chart", () => {
  const monday = dayIndex(2026, 1, 5);

  function eightSessions() {
    const all = minuteTape(monday, 8);
    const loaded = all.slice(3 * MINUTE_BARS);
    return { all, loaded, start: all[0] ?? 0, end: all[all.length - 1] ?? 0 };
  }

  /** Pixel x while the screen shows the last 150 loaded bars. */
  function place(loaded: readonly number[], time: number, width = 800, visible = 150): number {
    const logical = anchorLogical(time, loaded, "1m");
    if (logical == null) throw new Error("anchor has no logical index");
    const from = loaded.length - visible;
    return ((logical - from) * width) / visible;
  }

  it("counts five weekday sessions, and not the weekend, between two Mondays", () => {
    expect((monday + 3) % 7).toBe(0);
    const nextMonday = dayIndex(2026, 1, 12);
    const open = (day: number) => day * DAY_SECONDS - IST_OFFSET + SESSION_OPEN * 60;
    expect(barsBetween(open(monday), open(nextMonday), "1m")).toBe(5 * MINUTE_BARS);
  });

  it("draws a 15m rectangle on 1m when only the last 5 of 8 sessions are loaded", () => {
    const { loaded, start, end } = eightSessions();
    expect(loaded[0]).toBeGreaterThan(start);
    expect(anchorLogical(start, loaded, "1m")).toBe(-3 * MINUTE_BARS);
    const x1 = place(loaded, start);
    const x2 = place(loaded, end);
    expect(x1).toBeLessThan(0);
    expect(x2).toBeGreaterThan(0);
    expect(x2).toBeLessThan(800);
    const edge = clipSegment({ x1, y1: 120, x2, y2: 80 }, 800, 400);
    expect(edge?.x1).toBe(0);
    expect(edge && edge.x2).toBeGreaterThan(0);
  });

  it("draws a trend line on 1m when its start is before the loaded bars", () => {
    const { loaded, start, end } = eightSessions();
    const edge = clipSegment({ x1: place(loaded, start), y1: 40, x2: place(loaded, end), y2: 300 }, 800, 400);
    expect(edge?.x1).toBe(0);
    expect(edge && edge.x2).toBeGreaterThan(0);
    expect(edge && edge.y2).not.toBe(edge?.y1);
  });

  it("draws a Fibonacci on 1m when its start is before the loaded bars", () => {
    const { loaded, start, end } = eightSessions();
    const x1 = place(loaded, start);
    const x2 = place(loaded, end);
    const level = clipSegment({ x1: Math.min(x1, x2), y1: 160, x2: Math.max(x1, x2), y2: 160 }, 800, 400);
    expect(level?.x1).toBe(0);
    expect(level?.y1).toBe(160);
    expect(level && level.x2).toBeGreaterThan(0);
  });

  it("Zoom to loads history when the drawing start is not in the window, then scrolls to both anchors", () => {
    const { all, loaded, start, end } = eightSessions();
    expect(historyForZoom(loaded, start, end)).toEqual({ from: start, to: end });
    expect(historyForZoom(all, start, end)).toBeNull();
    const range = zoomLogical(all, start, end);
    expect(range?.from).toBeLessThanOrEqual(0);
    expect(range && range.to).toBeGreaterThanOrEqual(all.length - 1);
  });

  it("skips an NSE holiday when placing an anchor before the loaded window", () => {
    const holiday = dateOf(monday + 1);
    const tape = minuteTape(monday, 4, [holiday]);
    const loaded = tape.slice(MINUTE_BARS);
    expect(anchorLogical(tape[0] ?? 0, loaded, "1m", { holidays: [holiday] })).toBe(-MINUTE_BARS);
  });
});
