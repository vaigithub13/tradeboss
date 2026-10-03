import { describe, expect, it } from "vitest";

import { badgeView } from "./badge";
import type { LiveStatus } from "./protocol";

const st = (state: LiveStatus["state"], since: number | null = null): LiveStatus => ({
  state,
  since_last_tick_s: since,
  market: "open",
});

describe("badgeView", () => {
  it("live", () => {
    expect(badgeView(st("live", 0.4), "open", 0, 0)).toEqual({ label: "live", tone: "ok" });
  });

  it("stale counts up locally between status messages", () => {
    expect(badgeView(st("stale", 12), "open", 1000, 1000).label).toBe("stale (12s since last tick)");
    expect(badgeView(st("stale", 12), "open", 1000, 4400).label).toBe("stale (15s since last tick)");
    expect(badgeView(st("stale", 12), "open", 1000, 1000).tone).toBe("warn");
  });

  it("stale without a known age shows the local wait only", () => {
    expect(badgeView(st("stale", null), "open", 0, 3000).label).toBe("stale (3s since last tick)");
  });

  it("reconnecting: the feed's state, or our own link to the backend being down", () => {
    expect(badgeView(st("reconnecting"), "open", 0, 0).label).toBe("reconnecting…");
    expect(badgeView(st("live"), "closed", 0, 0).label).toBe("reconnecting…");
    expect(badgeView(st("live"), "connecting", 0, 0).label).toBe("reconnecting…");
    expect(badgeView(null, "open", null, 0).label).toBe("reconnecting…");
  });

  it("market closed, auth failure, lock, disabled", () => {
    expect(badgeView(st("closed"), "open", 0, 0)).toEqual({ label: "market closed", tone: "idle" });
    expect(badgeView(st("auth_failed"), "open", 0, 0).tone).toBe("bad");
    expect(badgeView(st("locked"), "open", 0, 0).label).toBe("feed in use elsewhere");
    expect(badgeView(st("disabled"), "open", 0, 0).label).toBe("live feed off");
  });
});
