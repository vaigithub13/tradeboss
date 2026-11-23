import { describe, expect, it } from "vitest";

import { feedIsStale } from "./feedStale";
import type { LiveStatus } from "./protocol";

const open: LiveStatus = { state: "live", since_last_tick_s: 40, market: "open", nifty_since_tick_s: 1 };

describe("feedIsStale", () => {
  it("stays quiet when Nifty ticked recently, even if another tick looks old", () => {
    expect(feedIsStale(open, 20_000, 21_000, null)).toBe(false);
  });

  it("is stale only after 15s without a Nifty tick in market hours", () => {
    expect(feedIsStale({ ...open, nifty_since_tick_s: 16 }, 1_000, 1_000, null)).toBe(true);
    expect(feedIsStale({ ...open, nifty_since_tick_s: 10 }, 0, 6_000, null)).toBe(true);
    expect(feedIsStale({ ...open, market: "closed", nifty_since_tick_s: 60 }, 0, 0, null)).toBe(false);
    expect(feedIsStale(open, 0, 0, 123)).toBe(false);
    expect(feedIsStale({ ...open, nifty_since_tick_s: null }, 0, 99_000, null)).toBe(false);
  });
});
