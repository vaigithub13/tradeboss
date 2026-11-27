/** CRT lines, labels and events for the Learn chart. Read from closed 5m candles. */

import { studyTip } from "./chartLook";
import { crtName, crtPairs, type CrtPair, type CrtTf } from "./crt";
import { atr14 } from "./sanity";
import type { StudyEvent, StudyLabel, StudyLine } from "./study";
import type { LearnBar } from "./structure";

export const CRT_CYAN = "#0891b2";
const M5 = 300;
const KEEP: Record<CrtTf, number> = { "75m": 6, D1: 3 };

export interface CrtMarks {
  lines: StudyLine[];
  labels: StudyLabel[];
  events: StudyEvent[];
}

function fmt(price: number): string {
  return (Math.round(price * 100) / 100).toLocaleString("en-IN");
}

/** The chart bar time at or before `time`, so marks land on any timeframe. */
function snapper(chart: readonly LearnBar[]): (time: number) => number {
  return (time) => {
    let lo = 0;
    let hi = chart.length - 1;
    let found = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (chart[mid]!.time <= time) {
        found = mid;
        lo = mid + 1;
      } else hi = mid - 1;
    }
    return found < 0 ? time : chart[found]!.time;
  };
}

function studyOf(pair: CrtPair, m5: readonly LearnBar[]): string | undefined {
  if (pair.status !== "crt" || pair.ext == null || pair.target == null) return undefined;
  const index = m5.findIndex((bar) => bar.time === pair.c2.end - M5);
  const atr = index >= 0 ? atr14(m5, index) : 0;
  const buy = pair.bias === "up";
  const sl = buy ? pair.ext - 0.1 * atr : pair.ext + 0.1 * atr;
  return studyTip(buy ? "buy" : "sell", pair.close2, sl, pair.target);
}

export function crtMarks(m5: readonly LearnBar[], now: number, chart: readonly LearnBar[], tfSeconds: number): CrtMarks {
  const out: CrtMarks = { lines: [], labels: [], events: [] };
  if (m5.length === 0) return out;
  const snap = snapper(chart.length ? chart : m5);
  const lastChart = (chart.length ? chart : m5).at(-1)!.time;
  const frames: CrtTf[] = tfSeconds <= 900 ? ["75m", "D1"] : ["D1"];
  for (const tf of frames) {
    const name = crtName(tf);
    const prev = tf === "75m" ? "previous" : "previous day";
    const pairs = crtPairs(m5, now, tf);
    const live = pairs.at(-1);
    const shown = pairs.filter((pair) => pair.status === "crt" || (pair === live && !pair.c2.complete)).slice(-KEEP[tf]);
    for (const pair of pairs.slice(-KEEP[tf] * 4)) {
      if (pair.sweepAt != null && pair.swept !== "both" && pair.sweepAt <= now) {
        out.events.push({
          time: pair.sweepAt, kind: "crt_sweep", text: `${name} forming: ${prev} ${pair.swept} swept`,
          detail: `The ${tf === "75m" ? "75m" : "daily"} candle traded beyond the candle-1 ${pair.swept} ${fmt(pair.swept === "high" ? pair.hi : pair.lo)}. CRT only if it closes back inside.`,
          color: CRT_CYAN, ref: { time: pair.sweepAt, price: pair.ext ?? pair.mid },
        });
      }
      if (pair.status === "breakout" || pair.status === "outside") {
        const at = pair.status === "outside" && !pair.c2.complete ? (pair.sweepAt ?? pair.c2.end) : pair.c2.end;
        out.events.push({
          time: at, kind: "crt_cancel",
          text: `${name} cancelled — ${pair.status === "outside" ? "took both sides" : "closed outside the range"}`,
          detail: `Candle 1 range ${fmt(pair.lo)}–${fmt(pair.hi)}. ${pair.status === "outside" ? "An outside bar is not a CRT." : `Candle 2 closed at ${fmt(pair.close2)}: a breakout, not a CRT.`}`,
          color: "#64748b", ref: { time: at, price: pair.close2 },
        });
      }
      if (pair.status === "crt" && pair.target != null) {
        out.events.push({
          time: pair.c2.end, kind: "crt_ok",
          text: `${name} ✓ — swept the ${pair.swept}, closed back inside at ${fmt(pair.close2)}; target ${fmt(pair.target)}`,
          detail: `${name} confirmed. Target = the other side ${fmt(pair.target)}.`,
          color: CRT_CYAN, ref: { time: pair.c2.end, price: pair.target },
        });
        if (pair.hitAt != null) {
          out.events.push({
            time: pair.hitAt, kind: "crt_target", text: `${name} target reached`,
            detail: `Price reached the ${name} target ${fmt(pair.target)}.`,
            color: CRT_CYAN, ref: { time: pair.hitAt, price: pair.target },
          });
        }
      }
    }
    for (const pair of shown) {
      const id = `crt-${tf}-${pair.c2.t}`;
      const tip = studyOf(pair, m5);
      const lineEnd = Math.min(pair.c2.end - M5, lastChart);
      for (const [price, word] of [[pair.hi, "high"], [pair.lo, "low"], [pair.mid, "50%"]] as const) {
        out.lines.push({
          id: `${id}-${word}`, kind: "crt_lvl", t1: snap(pair.c1.t), t2: snap(Math.max(lineEnd, pair.c1.t)), price,
          color: CRT_CYAN, dashed: word === "50%", label: `${name} ${word}`,
          detail: `${name} candle-1 range ${fmt(pair.lo)}–${fmt(pair.hi)}, 50% ${fmt(pair.mid)}. Candle 2 must sweep one side and close back inside.`,
          confirm: pair.c1.end, short: false,
        });
      }
      if (pair.swept === "high" || pair.swept === "low") {
        const high = pair.swept === "high";
        out.labels.push({
          id: `${id}-sweep`, kind: "crt_sweep", time: snap((pair.sweepAt ?? pair.c2.t) - M5), price: pair.ext ?? (high ? pair.hi : pair.lo),
          text: "CRT sweep", color: "#cffafe", textColor: "#155e75", pos: high ? "above" : "below",
          detail: `Candle 2 took the ${name} candle-1 ${pair.swept} ${fmt(high ? pair.hi : pair.lo)} (went to ${fmt(pair.ext ?? 0)}).`,
          confirm: pair.sweepAt ?? pair.c2.t,
        });
      }
      if (pair.status === "crt" && pair.target != null) {
        const up = pair.bias === "up";
        out.labels.push({
          id: `${id}-ok`, kind: "crt_ok", time: snap(pair.c2.end - M5), price: pair.ext ?? pair.close2,
          text: `CRT ✓ ${up ? "↑" : "↓"}`, color: CRT_CYAN, textColor: "#ffffff", pos: up ? "below" : "above",
          detail: `${name} confirmed: candle 2 swept the ${pair.swept} and closed back inside at ${fmt(pair.close2)}. Target = the other side ${fmt(pair.target)}.${pair.hitAt != null ? " Target reached." : ""}`,
          confirm: pair.c2.end, tip,
        });
        const end = Math.min(pair.hitAt ?? Math.min(pair.c3End, now), lastChart + M5);
        out.lines.push({
          id: `${id}-target`, kind: "crt_target", t1: snap(pair.c2.end), t2: snap(Math.max(end - M5, pair.c2.end)), price: pair.target,
          color: CRT_CYAN, dashed: true, label: pair.hitAt != null ? "CRT target ✓" : "CRT target",
          detail: `Target of the ${name}: ${fmt(pair.target)}${pair.hitAt != null ? " — reached" : ""}.`,
          confirm: pair.c2.end, short: false, tip,
        });
      }
    }
  }
  return out;
}
