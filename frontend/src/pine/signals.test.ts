import { describe, expect, it } from "vitest";

import { alertDedupeKey, extractSignals } from "./signals";

describe("pine signals", () => {
  const closes = [
    { time: 100, close: 100 },
    { time: 200, close: 110 },
    { time: 300, close: 105 },
  ];

  it("maps plotshape bool series to BUY CE / BUY PE", () => {
    const signals = extractSignals(
      {
        plots: {
          "BUY CE": {
            data: [
              { time: 100_000, value: false, title: "BUY CE" },
              { time: 200 * 1000, value: true, title: "BUY CE", options: { text: "BUY CE" } },
            ],
          },
          "BUY PE": {
            data: [{ time: 300 * 1000, value: true, title: "BUY PE", options: { text: "BUY PE" } }],
          },
          "#0": { data: [{ time: 200 * 1000, value: 42 }] },
        },
      },
      closes,
    );
    // 200*1000 ms is still < 1e11 → treated as seconds; use real ms (>1e11)
    const signalsMs = extractSignals(
      {
        plots: {
          "BUY CE": {
            data: [{ time: 1_700_000_200_000, value: true, title: "BUY CE", options: { text: "BUY CE" } }],
          },
          "BUY PE": {
            data: [{ time: 1_700_000_300_000, value: true, title: "BUY PE", options: { text: "BUY PE" } }],
          },
        },
      },
      [
        { time: 1_700_000_200, close: 110 },
        { time: 1_700_000_300, close: 105 },
      ],
    );
    expect(signalsMs.map((s) => ({ side: s.side, text: s.text, time: s.time }))).toEqual([
      { side: "BUY", text: "BUY CE", time: 1_700_000_200 },
      { side: "SELL", text: "BUY PE", time: 1_700_000_300 },
    ]);
    expect(signals.some((s) => s.text === "BUY CE")).toBe(true);
  });

  it("maps strategy entry long/short and exit fills", () => {
    const signals = extractSignals(
      {
        strategy: {
          opentrades: [
            { entry_id: "BUY CE", entry_time: 1_700_000_200_000, entry_price: 110, size: 1, status: "open" },
          ],
          closedtrades: [
            {
              entry_id: "BUY PE",
              entry_time: 1_700_000_100_000,
              entry_price: 100,
              exit_id: "EXIT",
              exit_time: 1_700_000_300_000,
              exit_price: 105,
              size: -1,
              status: "closed",
            },
          ],
        },
      },
      closes,
    );
    expect(signals.some((s) => s.side === "BUY" && s.text === "BUY CE")).toBe(true);
    expect(signals.some((s) => s.side === "SELL" && s.text === "BUY PE")).toBe(true);
    expect(signals.some((s) => s.side === "EXIT")).toBe(true);
  });

  it("dedupe key is unique per script+bar+side+text", () => {
    expect(alertDedupeKey("a", { time: 1, side: "BUY", text: "BUY CE" })).not.toBe(
      alertDedupeKey("a", { time: 1, side: "SELL", text: "BUY PE" }),
    );
  });
});
