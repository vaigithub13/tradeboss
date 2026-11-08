import type { OptionsChainResponse, OptionsLevel, OptionsMaxPain, OptionsPivot } from "../api/options";

export type LadderKind = "R" | "S" | "SPOT" | "PIVOT" | "MAX_PAIN";

export interface LadderItem {
  key: string;
  kind: LadderKind;
  /** Sort / display price (spot uses the live spot). */
  price: number;
  lvl?: OptionsLevel;
  pivot?: OptionsPivot;
  maxPain?: OptionsMaxPain;
}

/** Key Levels rows sorted by price descending (highest at top). */
export function buildKeyLevelsLadder(data: OptionsChainResponse): LadderItem[] {
  const spot = data.spot ?? 0;
  const items: LadderItem[] = [];
  for (const lvl of data.resistance ?? []) {
    items.push({ key: lvl.tag, kind: "R", price: lvl.strike, lvl });
  }
  for (const lvl of data.support ?? []) {
    items.push({ key: lvl.tag, kind: "S", price: lvl.strike, lvl });
  }
  if (data.pivot) {
    items.push({ key: "PIVOT", kind: "PIVOT", price: data.pivot.strike, pivot: data.pivot });
  }
  if (data.max_pain) {
    items.push({
      key: "MAX PAIN",
      kind: "MAX_PAIN",
      price: data.max_pain.strike,
      maxPain: data.max_pain,
    });
  }
  items.push({ key: "SPOT", kind: "SPOT", price: spot });
  items.sort((a, b) => b.price - a.price || a.key.localeCompare(b.key));
  return items;
}
