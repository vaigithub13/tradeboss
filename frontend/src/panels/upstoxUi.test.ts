import { describe, expect, it } from "vitest";

import type { InstrumentHit, SyncJob, TokenStatus } from "../api/client";
import { dataFlag, hitDetail, jobText, snapshotBadge, tokenBadge, tokenBlocksFetching } from "./upstoxUi";

const tok = (state: TokenStatus["state"], over: Partial<TokenStatus> = {}): TokenStatus => ({
  state,
  message: `msg-${state}`,
  expires_at: null,
  days_left: null,
  expires_soon: false,
  market_status: null,
  checked_at: null,
  ...over,
});

describe("tokenBadge", () => {
  it("says valid / invalid / expired in the words the UI promises", () => {
    expect(tokenBadge(tok("valid"), false).label).toBe("data token valid");
    expect(tokenBadge(tok("invalid"), false).label).toBe("data token invalid");
    expect(tokenBadge(tok("expired"), false).label).toBe("data token expired");
  });

  it("uses the backend message as the tooltip and red for bad tokens", () => {
    const b = tokenBadge(tok("expired"), false);
    expect(b.title).toBe("msg-expired");
    expect(b.dot).toContain("red");
    expect(tokenBadge(tok("valid"), false).dot).toContain("emerald");
  });

  it("warns (yellow) with the days left when the token is close to expiry", () => {
    const b = tokenBadge(tok("valid", { expires_soon: true, days_left: 5 }), false);
    expect(b.label).toBe("data token valid · 5d left");
    expect(b.dot).toContain("yellow");
  });

  it("covers missing / unverified / not yet checked / check failed", () => {
    expect(tokenBadge(tok("missing"), false).label).toBe("data token missing");
    expect(tokenBadge(tok("unreachable"), false).label).toBe("data token unverified");
    expect(tokenBadge(null, false).label).toBe("data token…");
    expect(tokenBadge(null, true).label).toBe("data token ?");
  });
});

describe("tokenBlocksFetching", () => {
  it("only a definitely bad / absent token blocks", () => {
    expect(tokenBlocksFetching(tok("invalid"))).toBe(true);
    expect(tokenBlocksFetching(tok("expired"))).toBe(true);
    expect(tokenBlocksFetching(tok("missing"))).toBe(true);
    expect(tokenBlocksFetching(tok("valid"))).toBe(false);
    expect(tokenBlocksFetching(tok("unreachable"))).toBe(false);
    expect(tokenBlocksFetching(null)).toBe(false);
  });
});

const job = (over: Partial<SyncJob>): SyncJob => ({
  id: "1", instrument_key: "k", symbol: "s", status: "running", windows_total: 0, windows_done: 0,
  bars_added: 0, message: "m", error: null, auth_error: false, started_at: "", finished_at: null, ...over,
});

describe("jobText", () => {
  it("shows progress, results and errors", () => {
    expect(jobText(job({}))).toBe("starting…");
    expect(jobText(job({ windows_total: 63, windows_done: 12 }))).toBe("fetching 1m history 12/63…");
    expect(jobText(job({ status: "done", bars_added: 439943 }))).toBe("done: 439,943 1m bars");
    expect(jobText(job({ status: "error", error: "Upstox rejected the data token" }))).toBe("failed: Upstox rejected the data token");
  });
});

const hit = (over: Partial<InstrumentHit>): InstrumentHit => ({
  instrument_key: "k", symbol: "S", name: "Name", kind: "equity", segment: "NSE_EQ", instrument_type: "EQ",
  expiry: null, strike: null, lot_size: null, underlying_key: null, symbol_id: "s", has_data: false, has_1m: false, ...over,
});

describe("search rows", () => {
  it("derivatives show expiry and lot size, others just the name", () => {
    expect(hitDetail(hit({}))).toBe("Name");
    expect(hitDetail(hit({ kind: "option", name: "NIFTY", expiry: "2026-10-06", lot_size: 65 }))).toBe("NIFTY · exp 2026-10-06 · lot 65");
  });

  it("flags 1m history / partial data / nothing stored", () => {
    expect(dataFlag(hit({ has_data: true, has_1m: true }))).toBe("1m");
    expect(dataFlag(hit({ has_data: true }))).toBe("partial");
    expect(dataFlag(hit({}))).toBe("fetch");
  });
});

describe("snapshotBadge", () => {
  it("is hidden while the snapshot is at most one trading day old", () => {
    expect(snapshotBadge(null)).toBeNull();
    expect(snapshotBadge({ latest: "2026-10-05", trading_days_old: 1, stale: false })).toBeNull();
  });

  it("warns with the age and the date when it is older", () => {
    const b = snapshotBadge({ latest: "2026-10-03", trading_days_old: 2, stale: true });
    expect(b?.label).toBe("snapshot 2 trading days old");
    expect(b?.title).toContain("2026-10-03");
  });

  it("warns when there is no snapshot at all", () => {
    expect(snapshotBadge({ latest: null, trading_days_old: null, stale: true })?.label).toBe("no instrument snapshot");
  });
});
