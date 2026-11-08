import type { OptionsChainResponse, OptionsLevel, OptionsPivot } from "../api/options";

export type LadderKind = "R" | "S" | "SPOT" | "PIVOT";

export interface LadderItem {
  key: string;
  kind: LadderKind;
  /** Sort / display price (spot uses the live spot). */
  price: number;
  lvl?: OptionsLevel;
  pivot?: OptionsPivot;
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
  items.push({ key: "SPOT", kind: "SPOT", price: spot });
  items.sort((a, b) => b.price - a.price || a.key.localeCompare(b.key));
  return items;
}
