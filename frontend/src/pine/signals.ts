/** Extract BUY / SELL / EXIT signals from a PineTS run context (plots, alerts, strategy). */

export type PineSide = "BUY" | "SELL" | "EXIT";

export interface PineSignal {
  time: number; // unix seconds (bar open)
  side: PineSide;
  text: string;
  price: number;
  source: "plotshape" | "plotchar" | "alert" | "alertcondition" | "strategy";
}

interface PlotPoint {
  time?: number;
  value?: unknown;
  title?: string;
  text?: string;
  options?: { text?: string };
}

interface PlotSeries {
  data?: PlotPoint[];
  title?: string;
  type?: string;
}

interface AlertEvent {
  type?: string;
  message?: string;
  title?: string;
  time?: number;
  bar_index?: number;
}

interface TradeLike {
  entry_id?: string;
  entry_time?: number;
  entry_price?: number;
  exit_id?: string;
  exit_time?: number;
  exit_price?: number;
  size?: number;
  status?: string;
  entry_comment?: string;
  exit_comment?: string;
}

function sideFromText(text: string, fallback: PineSide | null): PineSide | null {
  const u = text.toUpperCase();
  if (/\bEXIT\b/.test(u) || /\bCLOSE\b/.test(u)) return "EXIT";
  if (/\bSELL\b/.test(u) || /\bBUY\s*PE\b/.test(u) || /\bSHORT\b/.test(u) || /\bPE\b/.test(u)) {
    if (/\bBUY\s*PE\b/.test(u) || (/\bPE\b/.test(u) && /\bBUY\b/.test(u))) return "SELL";
    if (/\bSELL\b/.test(u) || /\bSHORT\b/.test(u)) return "SELL";
  }
  if (/\bBUY\b/.test(u) || /\bLONG\b/.test(u) || /\bCE\b/.test(u)) return "BUY";
  return fallback;
}

function msToSec(t: number): number {
  // PineTS plot times are ms; TradeBoss candles are unix seconds (~1e9).
  return t > 1e11 ? Math.floor(t / 1000) : t;
}

function priceAt(
  timeSec: number,
  closes: readonly { time: number; close: number }[],
): number {
  let price = closes[closes.length - 1]?.close ?? 0;
  for (const c of closes) {
    if (c.time <= timeSec) price = c.close;
    else break;
  }
  return price;
}

/** Collect signals from plots (plotshape/plotchar bool series), alerts, and strategy trades. */
export function extractSignals(
  ctx: {
    plots?: Record<string, PlotSeries>;
    alerts?: AlertEvent[];
    strategy?: {
      opentrades?: TradeLike[];
      closedtrades?: TradeLike[];
    };
  },
  closes: readonly { time: number; close: number }[],
): PineSignal[] {
  const out: PineSignal[] = [];
  const seen = new Set<string>();

  const push = (s: PineSignal): void => {
    const key = `${s.time}|${s.side}|${s.text}`;
    if (seen.has(key)) return;
    seen.add(key);
    out.push(s);
  };

  for (const [name, series] of Object.entries(ctx.plots ?? {})) {
    if (name.startsWith("__")) continue;
    const data = series.data ?? [];
    // plotshape/plotchar: boolean series (value === true). Skip numeric plot() series.
    const boolHits = data.filter((pt) => pt.value === true);
    if (boolHits.length === 0 && !data.some((pt) => pt.value === false)) continue;
    for (const pt of boolHits) {
      if (pt.time == null) continue;
      const text = String(pt.options?.text ?? pt.text ?? pt.title ?? series.title ?? name);
      const side = sideFromText(text, sideFromText(name, "BUY"));
      if (!side) continue;
      const time = msToSec(pt.time);
      push({
        time,
        side,
        text,
        price: priceAt(time, closes),
        source: name.toLowerCase().includes("char") ? "plotchar" : "plotshape",
      });
    }
  }

  for (const a of ctx.alerts ?? []) {
    const text = String(a.title ?? a.message ?? a.type ?? "alert");
    const side = sideFromText(text, "BUY") ?? "BUY";
    if (a.time == null) continue;
    const time = msToSec(a.time);
    push({
      time,
      side,
      text,
      price: priceAt(time, closes),
      source: a.type === "alertcondition" ? "alertcondition" : "alert",
    });
  }

  const trades = [...(ctx.strategy?.closedtrades ?? []), ...(ctx.strategy?.opentrades ?? [])];
  for (const t of trades) {
    if (t.entry_time != null) {
      const text = String(t.entry_comment ?? t.entry_id ?? "entry");
      const long = (t.size ?? 0) >= 0;
      const side = sideFromText(text, long ? "BUY" : "SELL") ?? (long ? "BUY" : "SELL");
      const time = msToSec(t.entry_time);
      push({
        time,
        side: side === "EXIT" ? (long ? "BUY" : "SELL") : side,
        text,
        price: t.entry_price ?? priceAt(time, closes),
        source: "strategy",
      });
    }
    if (t.status === "closed" && t.exit_time != null) {
      const text = String(t.exit_comment ?? t.exit_id ?? "EXIT");
      const time = msToSec(t.exit_time);
      push({
        time,
        side: "EXIT",
        text,
        price: t.exit_price ?? priceAt(time, closes),
        source: "strategy",
      });
    }
  }

  out.sort((a, b) => a.time - b.time || a.text.localeCompare(b.text));
  return out;
}

/** Dedupe key for alert log / sound: one alert per script+bar+side+text. */
export function alertDedupeKey(scriptId: string, signal: Pick<PineSignal, "time" | "side" | "text">): string {
  return `${scriptId}|${signal.time}|${signal.side}|${signal.text}`;
}
