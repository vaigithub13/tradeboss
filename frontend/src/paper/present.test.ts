import { describe, expect, it } from "vitest";

import { formatRupees, markLine, paperMarkers, paperRows, shortContract, type PaperSignal } from "./present";

const T0 = 1791171900; // 09:15 IST on 5 Oct 2026

function signal(over: Partial<PaperSignal>): PaperSignal {
  return {
    time: T0 + 300,
    decided_at_ms: 0,
    side: "BUY",
    index_price: 22600,
    reason: "log XZ crossed above 0: buy",
    symbol: "NIFTY 22600 CE 06 OCT 26",
    status: "filled",
    fill_source: "quote",
    fill_price: 100.5,
    note: "",
    ...over,
  };
}

describe("shortContract", () => {
  it("keeps the strike and the type", () => {
    expect(shortContract("NIFTY 22600 CE 06 OCT 26")).toBe("22600 CE");
    expect(shortContract(null)).toBe("");
  });
});

describe("paperMarkers", () => {
  it("marks filled entries, snapped to the candle that contains them", () => {
    const m = paperMarkers([signal({})], [T0, T0 + 300, T0 + 600])[0];
    expect(m?.time).toBe(T0 + 300);
    expect(m?.position).toBe("belowBar");
    expect(m?.text).toBe("L 22600 CE");
  });

  it("uses a different colour for a modelled fill", () => {
    const quote = paperMarkers([signal({})], [T0])[0];
    const modelled = paperMarkers([signal({ fill_source: "modelled" })], [T0])[0];
    expect(modelled?.color).not.toBe(quote?.color);
  });

  it("skips unfilled and exit records and anything before the first candle", () => {
    const out = paperMarkers(
      [signal({ status: "unfilled" }), signal({ side: "EXIT" }), signal({ time: T0 - 300 })],
      [T0],
    );
    expect(out).toEqual([]);
  });
});

describe("paperRows", () => {
  it("lists the newest first with the fill and its source", () => {
    const rows = paperRows([signal({ time: T0 + 300 }), signal({ time: T0 + 600, side: "SELL", fill_source: "modelled" })]);
    expect(rows[0]?.side).toBe("SELL");
    expect(rows[0]?.fill).toBe("100.50 (modelled)");
    expect(rows[1]?.time).toBe("09:20");
  });

  it("shows why a signal did not fill", () => {
    const row = paperRows([signal({ status: "unfilled", fill_price: null, fill_source: null, note: "no quote and no model price for the contract" })])[0];
    expect(row?.note).toBe("no quote and no model price for the contract");
    expect(row?.fill).toBe("—");
  });
});

describe("money and mark text", () => {
  it("signs the amount in rupees", () => {
    expect(formatRupees(1234.5)).toBe("+1,234.50");
    expect(formatRupees(-88)).toBe("−88.00");
    expect(formatRupees(null)).toBe("—");
  });

  it("says when there is no position or no bid", () => {
    expect(markLine(null)).toBe("no open position");
    expect(markLine({ symbol: "NIFTY 22600 PE 06 OCT 26", source: null, gross: null, net: null })).toBe("22600 PE: no live bid");
    expect(markLine({ symbol: "NIFTY 22600 PE 06 OCT 26", source: "quote", gross: 100, net: 80 })).toBe("22600 PE: +80.00 net (at bid)");
  });
});
