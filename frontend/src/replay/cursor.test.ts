/**
 * Replay cursor spec. Written before app code. Implementation is not in this slice.
 *
 * Public API under test (frontend/src/replay/cursor.ts, not written yet):
 *
 *   REPLAY_SPEEDS = [1, 2, 5, 10]
 *   barsUpTo(bars, cursor) -> bars with time <= cursor
 *   snapCursor(times, target) -> last time <= target, or null
 *   stepBar(times, cursor) -> the next time, or the last time when already there
 *   advanceBars(times, cursor, count) -> that many steps forward, clamped to the last bar
 *   nextTradeEntry(trades, cursor) -> the next entry_time strictly after cursor, or null
 *   reveal(trades, cursor) -> markers, stops, and cardId with nothing from the future
 *   indicatorTo(visible) -> last visible bar time, or null
 *   createReplay(bars, opts?) -> controller
 *     jumpTo, play, pause, step, tick(seconds), setSpeed, jumpTrade, exit
 *     strategy(visible) sees only bars up to the cursor
 *     live.start / live.stop are never called
 *     source "recording" throws (that slice is later)
 */
import { describe, expect, it } from "vitest";

import {
  REPLAY_SPEEDS,
  advanceBars,
  barsUpTo,
  createReplay,
  indicatorTo,
  nextTradeEntry,
  reveal,
  snapCursor,
  stepBar,
  type ReplayTrade,
} from "./cursor";

const bars = [100, 200, 300, 400, 500].map((time) => ({ time }));

const trades: ReplayTrade[] = [
  { id: 1, entry_time: 100, exit_time: 300, stop: 90 },
  { id: 2, entry_time: 400, exit_time: 500, stop: null },
];

describe("bars up to the cursor", () => {
  it("keeps bars that have started and drops every later bar", () => {
    expect(barsUpTo(bars, 300).map((bar) => bar.time)).toEqual([100, 200, 300]);
    expect(barsUpTo(bars, 250).map((bar) => bar.time)).toEqual([100, 200]);
    expect(barsUpTo(bars, 50)).toEqual([]);
    expect(indicatorTo(barsUpTo(bars, 300))).toBe(300);
    expect(indicatorTo([])).toBeNull();
  });

  it("snaps a clock time to the last bar at or before it", () => {
    expect(snapCursor(bars.map((bar) => bar.time), 250)).toBe(200);
    expect(snapCursor(bars.map((bar) => bar.time), 100)).toBe(100);
    expect(snapCursor(bars.map((bar) => bar.time), 900)).toBe(500);
    expect(snapCursor(bars.map((bar) => bar.time), 50)).toBeNull();
  });
});

describe("controls", () => {
  it("steps one bar, plays at 1, 2, 5, or 10 bars per second, and stops on the last bar", () => {
    expect(REPLAY_SPEEDS).toEqual([1, 2, 5, 10]);
    const times = bars.map((bar) => bar.time);
    expect(stepBar(times, 100)).toBe(200);
    expect(stepBar(times, 500)).toBe(500);
    expect(advanceBars(times, 100, 5)).toBe(500);
    expect(advanceBars(times, 200, 1)).toBe(300);
  });

  it("jumps to the next trade entry after the cursor", () => {
    expect(nextTradeEntry(trades, 50)).toBe(100);
    expect(nextTradeEntry(trades, 100)).toBe(400);
    expect(nextTradeEntry(trades, 300)).toBe(400);
    expect(nextTradeEntry(trades, 400)).toBeNull();
  });
});

describe("saved-run overlay", () => {
  it("draws a trade only after its bar, and never a future exit or stop", () => {
    expect(reveal(trades, 50)).toEqual({ markers: [], stops: [], cardId: null });
    expect(reveal(trades, 100)).toEqual({
      markers: [{ time: 100, kind: "entry", id: 1 }],
      stops: [{ id: 1, price: 90, from: 100 }],
      cardId: 1,
    });
    expect(reveal(trades, 200)).toEqual(reveal(trades, 100));
    expect(reveal(trades, 300)).toEqual({
      markers: [
        { time: 100, kind: "entry", id: 1 },
        { time: 300, kind: "exit", id: 1 },
      ],
      stops: [],
      cardId: 1,
    });
    expect(reveal(trades, 350).cardId).toBeNull();
    expect(reveal(trades, 350).markers.map((marker) => marker.kind)).toEqual(["entry", "exit"]);
    expect(reveal(trades, 400)).toEqual({
      markers: [
        { time: 100, kind: "entry", id: 1 },
        { time: 300, kind: "exit", id: 1 },
        { time: 400, kind: "entry", id: 2 },
      ],
      stops: [],
      cardId: 2,
    });
    expect(reveal(trades, 500).markers.some((marker) => marker.time === 500 && marker.kind === "exit")).toBe(true);
    expect(reveal(trades, 500).cardId).toBe(2);
  });
});

describe("replay controller", () => {
  it("plays, pauses, steps, and exits back to the full series without touching the live feed", () => {
    const calls: string[] = [];
    const seen: number[][] = [];
    const replay = createReplay(bars, {
      live: {
        start: () => calls.push("start"),
        stop: () => calls.push("stop"),
      },
      strategy: (visible) => {
        seen.push(visible.map((bar) => bar.time));
      },
    });

    expect(replay.active).toBe(false);
    replay.jumpTo(250);
    expect(replay.active).toBe(true);
    expect(replay.cursor).toBe(200);
    expect(replay.visible().map((bar) => bar.time)).toEqual([100, 200]);
    expect(seen.at(-1)).toEqual([100, 200]);

    replay.step();
    expect(replay.playing).toBe(false);
    expect(replay.cursor).toBe(300);
    expect(seen.at(-1)).toEqual([100, 200, 300]);

    replay.setSpeed(5);
    replay.play();
    replay.tick(1);
    expect(replay.cursor).toBe(500);
    replay.pause();
    const paused = replay.cursor;
    replay.tick(1);
    expect(replay.cursor).toBe(paused);

    replay.exit();
    expect(replay.active).toBe(false);
    expect(replay.cursor).toBeNull();
    expect(replay.visible()).toEqual(bars);
    expect(calls).toEqual([]);
  });

  it("rejects a speed outside 1, 2, 5, and 10, and refuses recorded sessions", () => {
    const replay = createReplay(bars);
    expect(() => replay.setSpeed(3)).toThrow(/1, 2, 5, 10/);
    expect(() => createReplay(bars, { source: "recording" })).toThrow(/later/);
  });

  it("jump to the next trade pauses on that entry", () => {
    const replay = createReplay(bars);
    replay.jumpTo(50);
    replay.play();
    replay.jumpTrade(trades);
    expect(replay.playing).toBe(false);
    expect(replay.cursor).toBe(100);
    replay.jumpTrade(trades);
    expect(replay.cursor).toBe(400);
    replay.jumpTrade(trades);
    expect(replay.cursor).toBe(400);
  });
});
