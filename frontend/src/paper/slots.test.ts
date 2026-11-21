import { describe, expect, it } from "vitest";

import {
  ACTIVE_PAPER_SLOTS,
  RETIRED_PAPER_SLOTS,
  isActivePaperSlot,
  isRetiredPaperSlot,
  slotStatusPill,
} from "./slots";

describe("paper slots", () => {
  it("treats 2,4,5,6 as active and 1,3 as retired", () => {
    expect([...ACTIVE_PAPER_SLOTS]).toEqual(["2", "4", "5", "6", "7"]);
    expect([...RETIRED_PAPER_SLOTS]).toEqual(["1", "3"]);
    expect(isActivePaperSlot("2")).toBe(true);
    expect(isActivePaperSlot("1")).toBe(false);
    expect(isRetiredPaperSlot("3")).toBe(true);
  });

  it("maps status to pills", () => {
    expect(slotStatusPill(null)).toBe("off");
    expect(slotStatusPill({ state: "stopped" })).toBe("off");
    expect(slotStatusPill({ state: "stopped", ended_by: "session-end" })).toBe("session ended");
    expect(slotStatusPill({ state: "starting" })).toBe("starting");
    expect(slotStatusPill({ state: "running", position: null })).toBe("waiting");
    expect(slotStatusPill({ state: "running", position: { symbol: "NIFTY 25000 CE" } })).toBe("in trade");
  });
});
