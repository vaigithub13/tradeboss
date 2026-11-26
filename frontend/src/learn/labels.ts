import { explain } from "./explain";
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
];

export function defaultToggles(): LayerToggles {
  return {
    swings: true, breaks: true, pdh: true, pdl: true, prevClose: true,
    week: true, cpr: true, round: true, opening: true,
  };
}

export interface LearnLabel {
  id: string;
  text: string;
  title: string;
}

export function visibleLabels(structure: Structure | null, levels: readonly LevelLine[], toggles: LayerToggles): LearnLabel[] {
  const out: LearnLabel[] = [];
  if (structure && toggles.swings) {
    for (const swing of structure.swings) {
      out.push({ id: `swing-${swing.time}-${swing.kind}`, text: swing.label, title: explain(swing.label) });
    }
  }
  if (structure && toggles.breaks) {
    for (const event of structure.events) {
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
  return out;
}
