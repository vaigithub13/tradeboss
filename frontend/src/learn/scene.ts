/** What the Learn chart draws. Label count and toggles are presentation only. */

import type { LearnColors } from "./theme";
import type { LearnBox, LearnMark, LearnSegment } from "./overlay";
import { textbookText, type Lesson } from "./lesson";
import { limitedStructure, type LayerToggles } from "./labels";
import type { LearnRead } from "./job";

export const SHADE = {
  accumulation: "rgba(120, 128, 140, 0.20)",
  manipulation: "rgba(224, 122, 47, 0.28)",
  up: "rgba(11, 122, 67, 0.16)",
  down: "rgba(198, 40, 40, 0.16)",
  textbook: "rgba(92, 107, 130, 0.14)",
} as const;

export interface SceneLine {
  price: number;
  title: string;
  color: string;
  dotted: boolean;
}

export interface Scene {
  lines: SceneLine[];
  segments: LearnSegment[];
  marks: LearnMark[];
  boxes: LearnBox[];
  amdNote: "No AMD today" | null;
}

export function buildScene(read: LearnRead, toggles: LayerToggles, colors: LearnColors, labelSwings: number): Scene {
  const lines: SceneLine[] = [];
  for (const level of read.levels) {
    if (!toggles[level.layer]) continue;
    lines.push({ price: level.price, title: level.label, color: colors.line, dotted: false });
  }
  if (toggles.liquidity) {
    for (const level of read.lesson.liquidity) {
      lines.push({ price: level.price, title: level.tag, color: colors.line, dotted: true });
    }
  }
  const shown = limitedStructure(read.structure, labelSwings);
  const segments: LearnSegment[] = toggles.breaks
    ? shown.events.map((event) => ({
      from: event.fromTime,
      to: event.time,
      price: event.price,
      text: event.kind === "bos" ? "BOS" : "CHoCH",
      color: event.direction === "up" ? colors.up : colors.down,
      short: true,
    }))
    : [];
  const marks: LearnMark[] = [];
  if (toggles.swings) {
    for (const swing of shown.swings) {
      marks.push({
        time: swing.time,
        price: swing.price,
        text: swing.label,
        color: colors.text,
        above: swing.kind === "high",
      });
    }
  }
  if (toggles.sweeps) {
    for (const sweep of read.lesson.sweeps) {
      marks.push(sweepMark(sweep, colors));
    }
  }
  if (toggles.amd) {
    for (const session of read.lesson.sessions) {
      const sweep = session.manipulation;
      if (!sweep) continue;
      const already = marks.some((mark) => mark.time === sweep.time && mark.text === sweep.text);
      if (!already) marks.push(sweepMark(sweep, colors));
    }
  }
  const boxes: LearnBox[] = [];
  if (toggles.amd) boxes.push(...amdBoxes(read.lesson));
  if (toggles.textbook) {
    for (const session of read.lesson.sessions) {
      if (!session.textbook) continue;
      boxes.push({
        from: session.textbook.from,
        to: session.textbook.to,
        priceLow: session.textbook.priceLow,
        priceHigh: session.textbook.priceHigh,
        color: SHADE.textbook,
        text: textbookText(session.textbook),
      });
    }
  }
  const latest = read.lesson.sessions[read.lesson.sessions.length - 1];
  return { lines, segments, marks, boxes, amdNote: toggles.amd ? latest?.note ?? null : null };
}

function sweepMark(sweep: { time: number; price: number; text: string; side: "high" | "low" }, colors: LearnColors): LearnMark {
  return {
    time: sweep.time,
    price: sweep.price,
    text: sweep.text,
    color: sweep.side === "high" ? colors.down : colors.up,
    above: sweep.side === "high",
  };
}

function amdBoxes(lesson: Lesson): LearnBox[] {
  const boxes: LearnBox[] = [];
  for (const session of lesson.sessions) {
    if (session.accumulation) {
      boxes.push({
        from: session.accumulation.from,
        to: session.accumulation.to,
        priceLow: session.accumulation.low,
        priceHigh: session.accumulation.high,
        color: SHADE.accumulation,
        text: "Accumulation",
      });
    }
    if (session.manipulation) {
      const sweep = session.manipulation;
      boxes.push({
        from: sweep.from,
        to: sweep.time,
        priceLow: Math.min(sweep.extreme, sweep.price),
        priceHigh: Math.max(sweep.extreme, sweep.price),
        color: SHADE.manipulation,
        text: "Manipulation",
      });
    }
    if (session.distribution && session.accumulation) {
      boxes.push({
        from: session.distribution.from,
        to: session.distribution.to,
        priceLow: session.accumulation.low,
        priceHigh: session.accumulation.high,
        color: session.distribution.direction === "up" ? SHADE.up : SHADE.down,
        text: "Distribution",
      });
    }
  }
  return boxes;
}
