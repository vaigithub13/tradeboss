/** Header views. The last choice is remembered in this browser. */

import type { StudyLayers } from "./study";
import { DEFAULT_LAYERS } from "./study";
import { ALL_TAGS, NO_TAGS, type TagToggles } from "./tags";

export type LearnPreset = "levels" | "amd" | "ict" | "full";

export const PRESET_KEY = "tradeboss-learn-view";

const OFF: StudyLayers = {
  session: false,
  amd: false,
  swing: false,
  structure: false,
  liquidity: false,
  fvg: false,
  fvgFilled: false,
  ob: false,
  ict: false,
  breaker: false,
  unicorn: false,
  ote: false,
  killzones: false,
  gaps: false,
  pattern: false,
  patternAll: false,
  round: false,
  notes: true,
  signals: false,
  events: true,
  zones: false,
};

export interface LearnView {
  layers: StudyLayers;
  tags: TagToggles;
}

export function viewFor(preset: LearnPreset): LearnView {
  if (preset === "amd") return { layers: { ...OFF, amd: true }, tags: NO_TAGS };
  if (preset === "ict") {
    return {
      layers: { ...OFF, fvg: true, ict: true, breaker: true, unicorn: true, ote: true, killzones: true, gaps: true },
      tags: NO_TAGS,
    };
  }
  if (preset === "full") return { layers: { ...DEFAULT_LAYERS, zones: true }, tags: ALL_TAGS };
  return { layers: { ...OFF, zones: true, swing: true }, tags: { ...NO_TAGS, pd: true } };
}

export function loadPreset(): LearnPreset {
  try {
    const value = localStorage.getItem(PRESET_KEY);
    if (value === "levels" || value === "amd" || value === "ict" || value === "full") return value;
  } catch {
    return "levels";
  }
  return "levels";
}

export function savePreset(preset: LearnPreset): void {
  try {
    localStorage.setItem(PRESET_KEY, preset);
  } catch {
    /* the chart still switches; the choice is just not kept */
  }
}
