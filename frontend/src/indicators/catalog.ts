import type { Timeframe } from "../api/client";

export const INDICATOR_TYPES = ["sma", "ema", "bb", "supertrend", "rsi", "stoch", "macd", "vwap", "fvg"] as const;
export type IndicatorType = (typeof INDICATOR_TYPES)[number];

export const SOURCES = ["open", "high", "low", "close", "hl2", "hlc3", "ohlc4"] as const;

export type ParamValue = number | string;
export type Params = Record<string, ParamValue>;

export interface ParamDef {
  key: string;
  label: string;
  kind: "int" | "float" | "source" | "choice";
  default: ParamValue;
  /** int: inclusive; float: exclusive lower bound unless inclusiveMin (mirrors the backend) */
  min?: number;
  max?: number;
  step?: number;
  inclusiveMin?: boolean;
  options?: { value: string; label: string }[];
}

export interface ColorDef {
  key: string;
  label: string;
  default: string;
  /** single-line indicators cycle through the palette so EMA 20 and EMA 50 differ */
  cycle?: boolean;
}

export interface IndicatorDef {
  type: IndicatorType;
  label: string;
  /** "price" = drawn over the candles; "separate" = own pane below */
  pane: "price" | "separate";
  params: ParamDef[];
  colors: ColorDef[];
  /** backend output names, in order */
  outputs: string[];
}

export interface IndicatorInstance {
  id: string;
  type: IndicatorType;
  params: Params;
  colors: Record<string, string>;
  visible: boolean;
}

export const PALETTE = ["#f6c343", "#2962ff", "#e91e63", "#00bcd4", "#9c27b0", "#ff9800", "#8bc34a"];

const MAX_LENGTH = 2000;
const MAX_MULT = 100;

const length = (def = 20, min = 1): ParamDef => ({
  key: "length", label: "Length", kind: "int", default: def, min, max: MAX_LENGTH, step: 1,
});
const source = (def = "close"): ParamDef => ({
  key: "source", label: "Source", kind: "source", default: def,
});

export const CATALOG: Record<IndicatorType, IndicatorDef> = {
  sma: {
    type: "sma", label: "SMA", pane: "price", params: [length(), source()],
    colors: [{ key: "line", label: "Line", default: PALETTE[0] ?? "#f6c343", cycle: true }],
    outputs: ["sma"],
  },
  ema: {
    type: "ema", label: "EMA", pane: "price", params: [length(), source()],
    colors: [{ key: "line", label: "Line", default: PALETTE[1] ?? "#2962ff", cycle: true }],
    outputs: ["ema"],
  },
  bb: {
    type: "bb", label: "Bollinger Bands", pane: "price",
    params: [
      length(),
      { key: "mult", label: "Multiplier", kind: "float", default: 2, min: 0, max: MAX_MULT, step: 0.1 },
      source(),
    ],
    colors: [
      { key: "basis", label: "Basis", default: "#ff9800" },
      { key: "band", label: "Upper / lower", default: "#2962ff" },
    ],
    outputs: ["basis", "upper", "lower"],
  },
  supertrend: {
    type: "supertrend", label: "Supertrend", pane: "price",
    params: [
      { key: "atr_length", label: "ATR length", kind: "int", default: 10, min: 1, max: MAX_LENGTH, step: 1 },
      { key: "multiplier", label: "Multiplier", kind: "float", default: 3, min: 0, max: MAX_MULT, step: 0.1 },
    ],
    colors: [
      { key: "up", label: "Uptrend", default: "#26a69a" },
      { key: "down", label: "Downtrend", default: "#ef5350" },
    ],
    outputs: ["supertrend", "direction"],
  },
  rsi: {
    type: "rsi", label: "RSI", pane: "separate", params: [length(14, 2), source()],
    colors: [{ key: "line", label: "Line", default: "#b388ff" }],
    outputs: ["rsi"],
  },
  stoch: {
    type: "stoch", label: "Stochastic", pane: "separate",
    params: [
      { key: "k_length", label: "%K Length", kind: "int", default: 14, min: 1, max: MAX_LENGTH, step: 1 },
      { key: "k_smoothing", label: "%K Smoothing", kind: "int", default: 1, min: 1, max: MAX_LENGTH, step: 1 },
      { key: "d_smoothing", label: "%D Smoothing", kind: "int", default: 3, min: 1, max: MAX_LENGTH, step: 1 },
      { key: "upper", label: "Upper band", kind: "int", default: 80, min: 0, max: 100, step: 1 },
      { key: "middle", label: "Middle band", kind: "int", default: 50, min: 0, max: 100, step: 1 },
      { key: "lower", label: "Lower band", kind: "int", default: 20, min: 0, max: 100, step: 1 },
      {
        key: "show_bands", label: "Bands", kind: "choice", default: "show",
        options: [{ value: "show", label: "Show" }, { value: "hide", label: "Hide" }],
      },
      {
        key: "show_background", label: "Background", kind: "choice", default: "show",
        options: [{ value: "show", label: "Show" }, { value: "hide", label: "Hide" }],
      },
      { key: "k_width", label: "%K width", kind: "int", default: 1, min: 1, max: 4, step: 1 },
      { key: "d_width", label: "%D width", kind: "int", default: 1, min: 1, max: 4, step: 1 },
      { key: "band_width", label: "Band width", kind: "int", default: 1, min: 1, max: 4, step: 1 },
    ],
    colors: [
      { key: "k", label: "%K", default: "#2962ff" },
      { key: "d", label: "%D", default: "#ff6d00" },
      { key: "band", label: "Bands", default: "#787b86" },
    ],
    outputs: ["k", "d"],
  },
  macd: {
    type: "macd", label: "MACD", pane: "separate",
    params: [
      { key: "fast", label: "Fast length", kind: "int", default: 12, min: 1, max: MAX_LENGTH, step: 1 },
      { key: "slow", label: "Slow length", kind: "int", default: 26, min: 1, max: MAX_LENGTH, step: 1 },
      { key: "signal", label: "Signal length", kind: "int", default: 9, min: 1, max: MAX_LENGTH, step: 1 },
      source(),
    ],
    colors: [
      { key: "macd", label: "MACD line", default: "#2962ff" },
      { key: "signal", label: "Signal line", default: "#ff9800" },
      { key: "histUp", label: "Histogram ≥ 0", default: "#26a69a" },
      { key: "histDown", label: "Histogram < 0", default: "#ef5350" },
    ],
    outputs: ["macd", "signal", "hist"],
  },
  vwap: {
    type: "vwap", label: "VWAP", pane: "price", params: [source("hlc3")],
    colors: [{ key: "line", label: "Line", default: "#00bcd4" }],
    outputs: ["vwap"],
  },
  fvg: {
    type: "fvg", label: "FVG", pane: "price",
    params: [
      { key: "min_gap", label: "Min gap", kind: "float", default: 0, min: 0, max: 1_000_000, step: 0.1, inclusiveMin: true },
      {
        key: "min_gap_mode", label: "Min gap in", kind: "choice", default: "points",
        options: [{ value: "points", label: "Points" }, { value: "percent", label: "Percent" }],
      },
      {
        key: "mitigation", label: "Mitigation", kind: "choice", default: "touch",
        options: [
          { value: "touch", label: "Touch" },
          { value: "half", label: "Half" },
          { value: "full", label: "Full" },
        ],
      },
      {
        key: "when_mitigated", label: "When mitigated", kind: "choice", default: "stop",
        options: [{ value: "stop", label: "Stop" }, { value: "fade", label: "Fade" }],
      },
      { key: "show_last", label: "Show last", kind: "int", default: 10, min: 1, max: 500, step: 1 },
      {
        key: "timeframe", label: "Timeframe", kind: "choice", default: "",
        options: [
          { value: "", label: "Chart" },
          ...(["1m", "3m", "5m", "15m", "30m", "1h", "1D", "1W"] as const).map((tf) => ({ value: tf, label: tf })),
        ],
      },
      {
        key: "session_gaps", label: "Overnight gaps", kind: "choice", default: "include",
        options: [{ value: "include", label: "Include" }, { value: "exclude", label: "Exclude" }],
      },
    ],
    colors: [
      { key: "bull", label: "Bullish", default: "#26a69a" },
      { key: "bear", label: "Bearish", default: "#ef5350" },
    ],
    outputs: ["bull_bottom", "bull_top", "bear_bottom", "bear_top"],
  },
};

export const isIndicatorType = (v: unknown): v is IndicatorType =>
  typeof v === "string" && (INDICATOR_TYPES as readonly string[]).includes(v);

const HEX = /^#[0-9a-fA-F]{6}$/;
export const isHexColor = (v: unknown): v is string => typeof v === "string" && HEX.test(v);

// ---------------------------------------------------------------- params

/** Parse + range-check one parameter; returns the clean value or null when invalid. */
export function parseParam(def: ParamDef, raw: string | number): ParamValue | null {
  if (def.kind === "source") {
    return typeof raw === "string" && (SOURCES as readonly string[]).includes(raw) ? raw : null;
  }
  if (def.kind === "choice") {
    const value = String(raw);
    return def.options?.some((option) => option.value === value) ? value : null;
  }
  if (typeof raw === "string" && raw.trim() === "") return null;
  const n = typeof raw === "number" ? raw : Number(raw);
  if (!Number.isFinite(n)) return null;
  if (def.kind === "int") {
    if (!Number.isInteger(n)) return null;
    if (def.min !== undefined && n < def.min) return null;
    if (def.max !== undefined && n > def.max) return null;
    return n;
  }
  if (def.min !== undefined && (def.inclusiveMin ? n < def.min : n <= def.min)) return null;
  if (def.max !== undefined && n > def.max) return null;
  return n;
}

export function defaultParams(type: IndicatorType): Params {
  return Object.fromEntries(CATALOG[type].params.map((p) => [p.key, p.default]));
}

/** Whole-object check (per-field rules plus cross-field rules). Returns an error message or null. */
export function validateParams(type: IndicatorType, params: Params): string | null {
  for (const def of CATALOG[type].params) {
    const raw = params[def.key];
    if (raw === undefined || parseParam(def, raw) === null) return `${def.label} is invalid`;
  }
  if (type === "macd" && Number(params["fast"]) >= Number(params["slow"])) {
    return "Fast length must be smaller than slow length";
  }
  return null;
}

// ---------------------------------------------------------------- instances

/** Smallest `${type}-N` not yet used. */
export function nextId(type: IndicatorType, existing: readonly IndicatorInstance[]): string {
  const used = new Set(existing.map((i) => i.id));
  for (let n = 1; ; n++) {
    const id = `${type}-${n}`;
    if (!used.has(id)) return id;
  }
}

export function createInstance(type: IndicatorType, existing: readonly IndicatorInstance[]): IndicatorInstance {
  const def = CATALOG[type];
  const copies = existing.filter((i) => i.type === type).length;
  const colors: Record<string, string> = {};
  for (const c of def.colors) {
    colors[c.key] = c.cycle ? (PALETTE[(PALETTE.indexOf(c.default) + copies) % PALETTE.length] ?? c.default) : c.default;
  }
  return { id: nextId(type, existing), type, params: defaultParams(type), colors, visible: true };
}

const fmtNum = (v: ParamValue | undefined): string => String(v ?? "");

/** "EMA (20, close)" */
export function indicatorName(inst: Pick<IndicatorInstance, "type" | "params">): string {
  const p = inst.params;
  const label = CATALOG[inst.type].label;
  switch (inst.type) {
    case "sma":
    case "ema":
    case "rsi":
      return `${label} (${fmtNum(p["length"])}, ${fmtNum(p["source"])})`;
    case "stoch":
      return `${label} (${fmtNum(p["k_length"])}, ${fmtNum(p["k_smoothing"])}, ${fmtNum(p["d_smoothing"])})`;
    case "bb":
      return `BB (${fmtNum(p["length"])}, ${fmtNum(p["mult"])}, ${fmtNum(p["source"])})`;
    case "supertrend":
      return `${label} (${fmtNum(p["atr_length"])}, ${fmtNum(p["multiplier"])})`;
    case "macd":
      return `${label} (${fmtNum(p["fast"])}, ${fmtNum(p["slow"])}, ${fmtNum(p["signal"])}, ${fmtNum(p["source"])})`;
    case "vwap":
      return `${label} (${fmtNum(p["source"])})`;
    case "fvg": {
      const tf = p["timeframe"] ? String(p["timeframe"]) : "chart";
      const gaps = p["session_gaps"] === "exclude" ? "overnight gaps excluded" : "overnight gaps included";
      return `${label} (${tf}, ${gaps})`;
    }
  }
}

export interface AvailabilityContext {
  timeframe: Timeframe;
  /** does the loaded range contain any volume? */
  hasVolume: boolean;
}

/** Why an indicator cannot be used right now (shown as a tooltip), or null when it can. */
export function unavailableReason(type: IndicatorType, ctx: AvailabilityContext): string | null {
  if (type !== "vwap") return null;
  if (ctx.timeframe === "1D" || ctx.timeframe === "1W") {
    return "VWAP resets every day, so it is intraday only (not available on 1D / 1W).";
  }
  if (!ctx.hasVolume) {
    return "VWAP needs volume. This symbol has none (index data such as Nifty), so it is disabled.";
  }
  return null;
}

/** Clean persisted JSON: drop unknown types, repair params / colours, keep ids unique. */
export function sanitizeInstances(raw: unknown): IndicatorInstance[] {
  if (!Array.isArray(raw)) return [];
  const out: IndicatorInstance[] = [];
  const seen = new Set<string>();
  for (const item of raw) {
    if (typeof item !== "object" || item === null) continue;
    const r = item as Record<string, unknown>;
    if (!isIndicatorType(r["type"]) || typeof r["id"] !== "string" || r["id"] === "" || seen.has(r["id"])) {
      continue;
    }
    const type = r["type"];
    const def = CATALOG[type];
    const rawParams = (typeof r["params"] === "object" && r["params"] !== null ? r["params"] : {}) as Record<string, unknown>;
    const params: Params = {};
    for (const p of def.params) {
      const given = rawParams[p.key];
      const parsed = typeof given === "string" || typeof given === "number" ? parseParam(p, given) : null;
      params[p.key] = parsed ?? p.default;
    }
    if (validateParams(type, params) !== null) Object.assign(params, defaultParams(type));
    const rawColors = (typeof r["colors"] === "object" && r["colors"] !== null ? r["colors"] : {}) as Record<string, unknown>;
    const fallback = createInstance(type, []).colors;
    const colors: Record<string, string> = {};
    for (const c of def.colors) {
      const given = rawColors[c.key];
      colors[c.key] = isHexColor(given) ? given : (fallback[c.key] ?? c.default);
    }
    seen.add(r["id"]);
    out.push({ id: r["id"], type, params, colors, visible: r["visible"] !== false });
  }
  return out;
}
