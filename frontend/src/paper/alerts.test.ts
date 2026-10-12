import { describe, expect, it } from "vitest";

import type { PaperStatus } from "../api/paper";
import { alertNotification, deskAlerts, slotAlerts, type SlotMemory } from "./alerts";

const DAY = "2026-10-12";
const T = 1_791_778_860_000; // 09:51 IST, ms

function entry(at: number, opts: { source?: string; status?: string; symbol?: string } = {}) {
  return {
    time: at / 1000 - 360, decided_at_ms: at, side: "SELL", index_price: 22510.55, reason: "", note: "",
    symbol: opts.symbol ?? "NIFTY 22500 PE 13 OCT 26", status: opts.status ?? "filled", fill_source: "quote",
    fill_price: 88.75, source: opts.source ?? "live",
  };
}

function trade(entryAt: number, exitAt: number, opts: { source?: string; reason?: string; net?: number } = {}) {
  return {
    symbol: "NIFTY 22500 PE 13 OCT 26", entry_at_ms: entryAt, exit_at_ms: exitAt, exit_price: 123.2,
    exit_reason: opts.reason ?? "premium_target", net: opts.net ?? 2174.07, source: opts.source ?? "live",
  };
}

type Over = Omit<Partial<PaperStatus>, "signals" | "trades"> & { signals?: unknown[]; trades?: unknown[] };

function status(over: Over = {}): PaperStatus {
  return {
    slot: "3", state: "running", ended_by: null, day: DAY, strategy: "log_xz", params: {}, wanted_keys: [],
    summary: null, position: null, mark: null, source: "live", ...over,
    signals: (over.signals ?? []) as PaperStatus["signals"],
    trades: (over.trades ?? []) as PaperStatus["trades"],
  };
}

/** Poll `steps` in order; the alerts of the last poll. */
function poll(steps: PaperStatus[], slotOn = true) {
  let memory: SlotMemory | undefined;
  let alerts: ReturnType<typeof slotAlerts>["alerts"] = [];
  for (const s of steps) ({ alerts, memory } = slotAlerts(memory, s, slotOn));
  return alerts;
}

describe("paper alerts: which events sound", () => {
  it("a new live entry and a new live exit each sound once, entry first", () => {
    const before = status();
    const filled = status({ signals: [entry(T)] });
    expect(poll([before, filled])).toEqual([
      { kind: "entry", slot: "3", action: "BUY", contract: "NIFTY 22500 PE 13 OCT 26", price: 88.75 },
    ]);
    const closed = status({ signals: [entry(T)], trades: [trade(T, T + 3_600_000)] });
    expect(poll([before, filled, closed])).toEqual([
      { kind: "exit", slot: "3", action: "SELL", contract: "NIFTY 22500 PE 13 OCT 26", price: 123.2,
        reason: "target", net: 2174.07 },
    ]);
    expect(poll([before, filled, closed, closed])).toEqual([]); // already seen
    // both inside one poll: the entry sounds first
    expect(poll([before, closed]).map((a) => a.kind)).toEqual(["entry", "exit"]);
  });

  it("an entry sounds when its signal turns from unfilled to filled (a stop order filled later)", () => {
    const working = status({ signals: [entry(T, { status: "unfilled" })] });
    expect(poll([status(), working])).toEqual([]);
    expect(poll([status(), working, status({ signals: [entry(T)] })])).toHaveLength(1);
  });

  it("nothing sounds for signals or trades marked replay (a late Start's catch-up)", () => {
    const caughtUp = status({ signals: [entry(T, { source: "replay" })], trades: [trade(T, T + 60_000, { source: "replay" })] });
    expect(poll([status(), caughtUp])).toEqual([]);
  });

  it("nothing sounds for a replay run (a Start after the close)", () => {
    const replay = (over: Over) => status({ ...over, source: "replay" });
    expect(poll([replay({}), replay({ signals: [entry(T)], trades: [trade(T, T + 60_000)] })])).toEqual([]);
  });

  it("the first poll, and the first poll after the slot became running, only take note", () => {
    const busy = status({ signals: [entry(T)], trades: [trade(T, T + 60_000)] });
    expect(poll([busy])).toEqual([]); // page loaded mid-day
    const starting = status({ state: "starting" });
    expect(poll([status({ state: "stopped" }), starting, busy])).toEqual([]); // Start caught up from the recording
    expect(poll([starting, busy, status({ signals: [entry(T), entry(T + 600_000)], trades: [trade(T, T + 60_000)] })]))
      .toHaveLength(1); // from then on, live entries sound
  });

  it("a new day starts fresh", () => {
    const yesterday = status({ day: "2026-10-09" });
    expect(poll([yesterday, status({ signals: [entry(T)] })])).toEqual([]);
  });

  it("an exit when the slot is stopped from the panel still sounds (it was running the poll before)", () => {
    const open = status({ signals: [entry(T)] });
    const stopped = status({ state: "stopped", ended_by: "stopped", signals: [entry(T)], trades: [trade(T, T + 60_000, { reason: "stop" })] });
    expect(poll([open, open, stopped]).map((a) => [a.kind, a.reason])).toEqual([["exit", "stopped by user"]]);
  });

  it("a slot switched off makes no sound, and switching it back on does not replay what it missed", () => {
    const filled = status({ signals: [entry(T)] });
    expect(poll([status(), filled], false)).toEqual([]);
    let memory: SlotMemory | undefined;
    ({ memory } = slotAlerts(memory, status(), true));
    ({ memory } = slotAlerts(memory, filled, false)); // off when it filled
    expect(slotAlerts(memory, filled, true).alerts).toEqual([]); // on again: nothing old
  });

  it("over the desk, each slot follows its own switch", () => {
    const s1 = (signals: unknown[]) => status({ slot: "1", signals });
    const s2 = (signals: unknown[]) => status({ slot: "2", signals });
    const on = (slot: string) => slot !== "2";
    const first = deskAlerts({}, [s1([]), s2([])], on);
    const second = deskAlerts(first.memory, [s1([entry(T)]), s2([entry(T)])], on);
    expect(second.alerts.map((a) => a.slot)).toEqual(["1"]);
  });
});

describe("paper alert notifications", () => {
  it("entry: slot, BUY, contract and price; exit: also the reason and net", () => {
    expect(alertNotification({ kind: "entry", slot: "3", action: "BUY", contract: "NIFTY 22500 PE 13 OCT 26", price: 88.75 }))
      .toEqual({ title: "Slot 3 · BUY NIFTY 22500 PE 13 OCT 26", body: "Entry @ 88.75" });
    expect(alertNotification({ kind: "exit", slot: "4", action: "SELL", contract: "NIFTY 22500 PE 13 OCT 26",
      price: 66.8, reason: "stop", net: -1084.95 }))
      .toEqual({ title: "Slot 4 · SELL NIFTY 22500 PE 13 OCT 26", body: "Exit @ 66.80 · stop · net −₹1,084.95" });
  });
});
