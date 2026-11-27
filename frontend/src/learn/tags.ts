/** Right-axis tags. Nearby prices share one tag, and only six are drawn. */

export type TagKind = "bsl" | "eq" | "pd" | "week" | "or" | "round";

export interface TagToggles {
  bsl: boolean;
  eq: boolean;
  pd: boolean;
  week: boolean;
  or: boolean;
  round: boolean;
}

export const ALL_TAGS: TagToggles = { bsl: true, eq: true, pd: true, week: true, or: true, round: true };
export const NO_TAGS: TagToggles = { bsl: false, eq: false, pd: false, week: false, or: false, round: false };

export interface TagInput {
  kind: TagKind;
  text: string;
  price: number;
  detail: string;
}

export interface AxisTag {
  price: number;
  text: string;
  detail: string;
  kinds: TagKind[];
  strength: number;
  distance: number;
}

const MERGE = 0.15;

export function tagInputs(
  lines: readonly { kind: string; label: string; price: number; detail: string }[],
  rounds: readonly { price: number; major: boolean }[],
  toggles: TagToggles,
  atr: number,
  last: number,
): TagInput[] {
  const out: TagInput[] = [];
  for (const line of lines) {
    if (toggles.pd && (line.kind === "pdh" || line.kind === "pdl")) {
      out.push({ kind: "pd", text: line.kind === "pdh" ? "PDH" : "PDL", price: line.price, detail: line.detail });
    }
    if (toggles.bsl && (line.label === "BSL" || line.label === "SSL")) {
      out.push({ kind: "bsl", text: line.label, price: line.price, detail: line.detail });
    }
    if (toggles.eq && (line.kind === "eqh" || line.kind === "eql")) {
      out.push({ kind: "eq", text: line.kind === "eqh" ? "EQH" : "EQL", price: line.price, detail: line.detail });
    }
    if (toggles.week && (line.kind === "pwh" || line.kind === "pwl")) {
      out.push({ kind: "week", text: line.kind === "pwh" ? "PWH" : "PWL", price: line.price, detail: line.detail });
    }
    if (toggles.or && (line.kind === "or_h" || line.kind === "or_l")) {
      out.push({ kind: "or", text: line.kind === "or_h" ? "ORH" : "ORL", price: line.price, detail: line.detail });
    }
  }
  if (toggles.round) {
    for (const mark of rounds) {
      if (atr > 0 && Math.abs(mark.price - last) > 3 * atr) continue;
      out.push({ kind: "round", text: mark.major ? "100" : "50", price: mark.price, detail: `Round number ${mark.price}` });
    }
  }
  return out;
}

export function mergeTags(levels: readonly TagInput[], atr: number, last: number): { shown: AxisTag[]; rest: AxisTag[] } {
  const sorted = [...levels].filter((level) => Number.isFinite(level.price)).sort((a, b) => a.price - b.price);
  const groups: TagInput[][] = [];
  const step = Math.max(atr, 0) * MERGE;
  for (const level of sorted) {
    const group = groups[groups.length - 1];
    const prev = group?.[group.length - 1];
    if (!group || !prev || (step > 0 && level.price - prev.price > step)) groups.push([level]);
    else group.push(level);
  }
  const tags: AxisTag[] = groups.map((group) => {
    const price = group.reduce((sum, level) => sum + level.price, 0) / group.length;
    const texts = [...new Set(group.map((level) => level.text))];
    return {
      price,
      text: texts.join(" · "),
      detail: group.map((level) => level.detail).join(" "),
      kinds: [...new Set(group.map((level) => level.kind))],
      strength: group.length,
      distance: Math.abs(price - last),
    };
  });
  const ranked = [...tags].sort((a, b) => a.distance - b.distance || b.strength - a.strength);
  return { shown: ranked.slice(0, 6), rest: ranked.slice(6) };
}
