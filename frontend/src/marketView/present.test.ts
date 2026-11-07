import { describe, expect, it } from "vitest";

import { biasColor, factorColor, formatRange, istDate, MV_DOWN, MV_NA, MV_UP, rateText, shouldOpenPanel, viewLevels } from "./present";

// Mon 9 Nov 2026 08:50 IST = 03:20 UTC
const AT_0850 = Date.UTC(2026, 10, 9, 3, 20) / 1000;
const AT_0930 = Date.UTC(2026, 10, 9, 4, 0) / 1000;

describe("market view presentation", () => {
  it("colours factors and bias", () => {
    expect(factorColor({ score: 1, available: true })).toBe(MV_UP);
    expect(factorColor({ score: -1, available: true })).toBe(MV_DOWN);
    expect(factorColor({ score: null, available: false })).toBe(MV_NA);
    expect(biasColor(-2)).toBe(MV_DOWN);
  });

  it("opens the panel before 09:15 IST unless collapsed today", () => {
    expect(istDate(AT_0850)).toBe("2026-11-09");
    expect(shouldOpenPanel(AT_0850, null)).toBe(true);
    expect(shouldOpenPanel(AT_0850, "2026-11-09")).toBe(false);
    expect(shouldOpenPanel(AT_0850, "2026-11-06")).toBe(true);
    expect(shouldOpenPanel(AT_0930, null)).toBe(false);
  });

  it("maps levels to price lines and formats ranges and rates", () => {
    expect(viewLevels([{ key: "pdh", label: "PDH", price: 25180 }])).toEqual([{ price: 25180, title: "PDH", color: MV_UP }]);
    expect(formatRange([24800.4, 25250.6])).toBe("24,800–25,251");
    expect(formatRange(null)).toBe("–");
    expect(rateText({ n: 4, rate: 0.75 })).toBe("75% of 4 days");
    expect(rateText({ n: 0, rate: null })).toBe("– (0 days)");
  });
});
