import { describe, expect, it } from "vitest";

import {
  CATALOG,
  INDICATOR_TYPES,
  createInstance,
  defaultParams,
  indicatorName,
  menuIndicatorTypes,
  nextId,
  parseParam,
  sanitizeInstances,
  unavailableReason,
  validateParams,
  type IndicatorInstance,
} from "./catalog";

const def = (type: Parameters<typeof defaultParams>[0], key: string) => {
  const d = CATALOG[type].params.find((p) => p.key === key);
  if (!d) throw new Error(`no param ${type}.${key}`);
  return d;
};

describe("defaults mirror the backend registry", () => {
  it.each([
    ["sma", { length: 20, source: "close" }],
    ["ema", { length: 20, source: "close" }],
    ["bb", { length: 20, mult: 2, source: "close" }],
    ["supertrend", { atr_length: 10, multiplier: 3 }],
    ["rsi", { length: 14, source: "close" }],
    ["stoch", {
      k_length: 14, k_smoothing: 1, d_smoothing: 3,
      upper: 80, middle: 50, lower: 20,
      show_bands: "show", show_background: "show",
      k_width: 1, d_width: 1, band_width: 1,
    }],
    ["macd", { fast: 12, slow: 26, signal: 9, source: "close" }],
    ["vwap", { source: "hlc3" }],
    ["vwap_fut", { source: "hlc3", roll_days: 2, roll_on_volume: "on" }],
  ] as const)("%s", (type, expected) => {
    expect(defaultParams(type)).toEqual(expected);
  });

  it("every type has outputs and colours; RSI, Stochastic, and MACD are separate panes", () => {
    for (const t of INDICATOR_TYPES) {
      expect(CATALOG[t].outputs.length).toBeGreaterThan(0);
      expect(CATALOG[t].colors.length).toBeGreaterThan(0);
    }
    expect(INDICATOR_TYPES.filter((t) => CATALOG[t].pane === "separate")).toEqual(["rsi", "stoch", "macd"]);
  });
});

describe("parseParam", () => {
  it("accepts valid ints and rejects out-of-range / fractional / empty", () => {
    const len = def("ema", "length");
    expect(parseParam(len, "50")).toBe(50);
    expect(parseParam(len, 1)).toBe(1);
    expect(parseParam(len, 0)).toBeNull();
    expect(parseParam(len, -5)).toBeNull();
    expect(parseParam(len, 2.5)).toBeNull();
    expect(parseParam(len, 100000)).toBeNull();
    expect(parseParam(len, "")).toBeNull();
    expect(parseParam(len, "abc")).toBeNull();
  });

  it("float multipliers must be > 0", () => {
    const mult = def("supertrend", "multiplier");
    expect(parseParam(mult, "2.5")).toBe(2.5);
    expect(parseParam(mult, 0)).toBeNull();
    expect(parseParam(mult, -1)).toBeNull();
  });

  it("Stochastic lengths are at least 1 and band levels stay inside 0–100", () => {
    expect(parseParam(def("stoch", "k_length"), 1)).toBe(1);
    expect(parseParam(def("stoch", "k_smoothing"), 0)).toBeNull();
    expect(parseParam(def("stoch", "d_smoothing"), 3)).toBe(3);
    expect(parseParam(def("stoch", "upper"), 0)).toBe(0);
    expect(parseParam(def("stoch", "upper"), 100)).toBe(100);
    expect(parseParam(def("stoch", "lower"), 101)).toBeNull();
    expect(parseParam(def("stoch", "k_width"), 4)).toBe(4);
    expect(parseParam(def("stoch", "k_width"), 5)).toBeNull();
    expect(parseParam(def("stoch", "show_bands"), "hide")).toBe("hide");
    expect(parseParam(def("stoch", "show_bands"), "maybe")).toBeNull();
  });

  it("RSI length must be at least 2; sources must be known", () => {
    expect(parseParam(def("rsi", "length"), 1)).toBeNull();
    expect(parseParam(def("rsi", "length"), 2)).toBe(2);
    expect(parseParam(def("ema", "source"), "hlc3")).toBe("hlc3");
    expect(parseParam(def("ema", "source"), "volume")).toBeNull();
  });
});

describe("validateParams", () => {
  it("MACD fast must be smaller than slow", () => {
    expect(validateParams("macd", { fast: 12, slow: 26, signal: 9, source: "close" })).toBeNull();
    expect(validateParams("macd", { fast: 26, slow: 12, signal: 9, source: "close" })).toMatch(/smaller/);
    expect(validateParams("macd", { fast: 12, slow: 12, signal: 9, source: "close" })).not.toBeNull();
  });

  it("missing or bad fields are reported", () => {
    expect(validateParams("ema", { source: "close" })).toMatch(/Length/);
    expect(validateParams("ema", { length: 0, source: "close" })).not.toBeNull();
  });
});

describe("instances", () => {
  it("ids are unique and reuse freed numbers", () => {
    const a = createInstance("ema", []);
    const b = createInstance("ema", [a]);
    expect([a.id, b.id]).toEqual(["ema-1", "ema-2"]);
    expect(nextId("ema", [b])).toBe("ema-1");
    expect(createInstance("rsi", [a, b]).id).toBe("rsi-1");
  });

  it("copies of a single-line indicator get different colours (EMA 20 + EMA 50)", () => {
    const a = createInstance("ema", []);
    const b = createInstance("ema", [a]);
    const c = createInstance("ema", [a, b]);
    expect(new Set([a.colors["line"], b.colors["line"], c.colors["line"]]).size).toBe(3);
  });

  it("names show the parameters", () => {
    expect(indicatorName({ type: "ema", params: { length: 20, source: "close" } })).toBe("EMA (20, close)");
    expect(indicatorName({ type: "bb", params: { length: 20, mult: 2, source: "close" } })).toBe("BB (20, 2, close)");
    expect(indicatorName({ type: "supertrend", params: { atr_length: 10, multiplier: 3 } })).toBe("Supertrend (10, 3)");
    expect(indicatorName({ type: "macd", params: { fast: 12, slow: 26, signal: 9, source: "close" } })).toBe(
      "MACD (12, 26, 9, close)",
    );
    expect(indicatorName({ type: "vwap", params: { source: "hlc3" } })).toBe("VWAP (hlc3)");
    expect(indicatorName({ type: "vwap_fut", params: { source: "hlc3", roll_days: 2, roll_on_volume: "on" } })).toBe(
      "VWAP (futures volume)",
    );
    expect(indicatorName({ type: "stoch", params: { k_length: 14, k_smoothing: 1, d_smoothing: 3 } })).toBe(
      "Stochastic (14, 1, 3)",
    );
    expect(indicatorName({ type: "stoch", params: { k_length: 5, k_smoothing: 3, d_smoothing: 3 } })).toBe(
      "Stochastic (5, 3, 3)",
    );
  });
});

describe("VWAP availability", () => {
  it("is disabled on zero-volume symbols (Nifty) with an explanation", () => {
    const reason = unavailableReason("vwap", { timeframe: "15m", hasVolume: false });
    expect(reason).toMatch(/volume/i);
  });

  it("is disabled on 1D / 1W (intraday only)", () => {
    expect(unavailableReason("vwap", { timeframe: "1D", hasVolume: true })).toMatch(/intraday/i);
    expect(unavailableReason("vwap", { timeframe: "1W", hasVolume: true })).toMatch(/intraday/i);
  });

  it("is enabled on intraday data with volume; other indicators never need volume", () => {
    expect(unavailableReason("vwap", { timeframe: "5m", hasVolume: true })).toBeNull();
    for (const t of INDICATOR_TYPES.filter((x) => x !== "vwap" && x !== "vwap_fut")) {
      expect(unavailableReason(t, { timeframe: "1D", hasVolume: false })).toBeNull();
    }
  });
});

describe("VWAP (futures volume) on the Nifty index", () => {
  it("replaces the price VWAP in the menu and is available on intraday Nifty", () => {
    expect(menuIndicatorTypes("NIFTY50")).toContain("vwap_fut");
    expect(menuIndicatorTypes("NIFTY50")).not.toContain("vwap");
    expect(menuIndicatorTypes("RELIANCE")).toContain("vwap");
    expect(menuIndicatorTypes("RELIANCE")).not.toContain("vwap_fut");
    expect(unavailableReason("vwap_fut", { timeframe: "5m", hasVolume: false, symbol: "NIFTY50" })).toBeNull();
    expect(unavailableReason("vwap_fut", { timeframe: "5m", hasVolume: true, symbol: "RELIANCE" })).toMatch(/Nifty/i);
    expect(unavailableReason("vwap_fut", { timeframe: "1D", hasVolume: false, symbol: "NIFTY50" })).toMatch(/intraday/i);
  });
});

describe("sanitizeInstances (persisted data is untrusted)", () => {
  const good: IndicatorInstance = {
    id: "ema-1",
    type: "ema",
    params: { length: 50, source: "hl2" },
    colors: { line: "#112233" },
    visible: false,
  };

  it("keeps a valid instance unchanged", () => {
    expect(sanitizeInstances([good])).toEqual([good]);
  });

  it("drops junk, unknown types and duplicate ids", () => {
    const out = sanitizeInstances([null, 5, "x", { id: "a", type: "nope" }, { id: "", type: "ema" }, good, good]);
    expect(out.map((i) => i.id)).toEqual(["ema-1"]);
    expect(sanitizeInstances("garbage")).toEqual([]);
    expect(sanitizeInstances(undefined)).toEqual([]);
  });

  it("repairs bad params and colours with defaults", () => {
    const [inst] = sanitizeInstances([
      { id: "ema-1", type: "ema", params: { length: -3, source: "volume" }, colors: { line: "red" } },
    ]);
    expect(inst?.params).toEqual({ length: 20, source: "close" });
    expect(inst?.colors["line"]).toMatch(/^#[0-9a-f]{6}$/i);
    expect(inst?.visible).toBe(true);
  });

  it("resets an invalid MACD combination", () => {
    const [inst] = sanitizeInstances([{ id: "m", type: "macd", params: { fast: 30, slow: 10, signal: 9, source: "close" } }]);
    expect(inst?.params).toEqual({ fast: 12, slow: 26, signal: 9, source: "close" });
  });
});
