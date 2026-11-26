import { explain } from "./explain";
import type { Lesson } from "./lesson";
import type { LevelLine } from "./levels";
import type { Structure } from "./structure";

export interface LayerToggles {
  swings: boolean;
  breaks: boolean;
  pdh: boolean;
  pdl: boolean;
  prevClose: boolean;
  week: boolean;
  cpr: boolean;
  round: boolean;
  opening: boolean;
  liquidity: boolean;
  sweeps: boolean;
  amd: boolean;
  textbook: boolean;
}

export const LAYER_TOGGLES: { key: keyof LayerToggles; label: string }[] = [
  { key: "swings", label: "Swings" },
  { key: "breaks", label: "BOS / CHoCH" },
  { key: "pdh", label: "PDH" },
  { key: "pdl", label: "PDL" },
  { key: "prevClose", label: "Prev close" },
  { key: "week", label: "Week" },
  { key: "cpr", label: "CPR" },
  { key: "round", label: "Round 100" },
  { key: "opening", label: "Opening range" },
  { key: "liquidity", label: "Liquidity" },
  { key: "sweeps", label: "Sweeps" },
  { key: "amd", label: "AMD" },
  { key: "textbook", label: "Textbook entry" },
];

export const DEFAULT_LABEL_SWINGS = 12;

export function defaultToggles(): LayerToggles {
  return {
    swings: true, breaks: true, pdh: true, pdl: true, prevClose: true,
    week: true, cpr: true, round: true, opening: true,
    liquidity: true, sweeps: true, amd: true, textbook: true,
  };
}

export interface LearnLabel {
  id: string;
  text: string;
  title: string;
}

/** Swing labels and the breaks that belong to them, limited to the last `count`. */
export function limitedStructure(structure: Structure, count = DEFAULT_LABEL_SWINGS): Structure {
  const swings = count <= 0 ? [] : structure.swings.slice(-count);
  const ids = new Set(swings.map((swing) => swing.index));
  return {
    ...structure,
    swings,
    events: structure.events.filter((event) => ids.has(event.swingIndex)),
  };
}

export function visibleLabels(
  structure: Structure | null,
  levels: readonly LevelLine[],
  toggles: LayerToggles,
  lesson?: Lesson | null,
  labelSwings = DEFAULT_LABEL_SWINGS,
): LearnLabel[] {
  const out: LearnLabel[] = [];
  const shown = structure ? limitedStructure(structure, labelSwings) : null;
  if (shown && toggles.swings) {
    for (const swing of shown.swings) {
      out.push({ id: `swing-${swing.time}-${swing.kind}`, text: swing.label, title: explain(swing.label) });
    }
  }
  if (shown && toggles.breaks) {
    for (const event of shown.events) {
      out.push({
        id: `break-${event.time}-${event.kind}`,
        text: event.kind === "bos" ? "BOS" : "CHoCH",
        title: explain(event.kind),
      });
    }
  }
  for (const level of levels) {
    if (!toggles[level.layer]) continue;
    out.push({ id: level.id, text: level.label, title: explain(level.layer) });
  }
  if (lesson && toggles.liquidity) {
    for (const level of lesson.liquidity) {
      out.push({
        id: level.id,
        text: level.tag,
        title: explain(level.tag),
      });
    }
  }
  if (lesson && toggles.sweeps) {
    for (const sweep of lesson.sweeps) {
      out.push({ id: `sweep-${sweep.time}-${sweep.name}`, text: sweep.text, title: explain("sweep") });
    }
  }
  const latest = lesson?.sessions[lesson.sessions.length - 1];
  if (latest && toggles.amd) {
    if (latest.accumulation) out.push({ id: `acc-${latest.day}`, text: "Accumulation", title: explain("Accumulation") });
    if (latest.manipulation) out.push({ id: `man-${latest.day}`, text: latest.manipulation.text, title: explain("Manipulation") });
    if (latest.distribution) out.push({ id: `dist-${latest.day}`, text: "Distribution", title: explain("Distribution") });
    if (latest.note) out.push({ id: `note-${latest.day}`, text: latest.note, title: explain(latest.note) });
  }
  if (latest?.textbook && toggles.textbook) {
    out.push({ id: `tb-${latest.day}`, text: "Textbook entry (learning)", title: explain("textbook") });
    if (latest.textbook.outcome) {
      out.push({ id: `tb-out-${latest.day}`, text: latest.textbook.outcome, title: explain(latest.textbook.outcome) });
    }
  }
  return out;
}
