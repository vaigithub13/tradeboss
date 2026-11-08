import type { OptionsChainResponse, OptionsLevel, OptionsMaxPain, OptionsPivot } from "../api/options";

export type LadderKind = "R" | "S" | "SPOT" | "PIVOT" | "MAX_PAIN" | "RS_PIVOT";

export interface LadderItem {
  key: string;
  kind: LadderKind;
  /** Combined label e.g. "R1 / PIVOT" when pivot shares the strike. */
  label?: string;
  /** Sort / display price (spot uses the live spot). */
  price: number;
  lvl?: OptionsLevel;
  pivot?: OptionsPivot;
  maxPain?: OptionsMaxPain;
}

/** Key Levels rows sorted by price descending (highest at top). */
export function buildKeyLevelsLadder(data: OptionsChainResponse): LadderItem[] {
  const spot = data.spot ?? 0;
  const pivotStrike = data.pivot?.strike;
  const items: LadderItem[] = [];
  const consumedPivot = { yes: false };

  const pushRs = (lvl: OptionsLevel, kind: "R" | "S") => {
    const isPivot = pivotStrike != null && Math.abs(lvl.strike - pivotStrike) < 1e-6;
    if (isPivot && data.pivot) {
      consumedPivot.yes = true;
      items.push({
        key: `${lvl.tag}/PIVOT`,
        kind: "RS_PIVOT",
        label: `${lvl.tag} / PIVOT`,
        price: lvl.strike,
        lvl,
        pivot: data.pivot,
      });
      return;
    }
    items.push({ key: lvl.tag, kind, price: lvl.strike, lvl, label: lvl.tag });
  };

  for (const lvl of data.resistance ?? []) {
    pushRs(lvl, "R");
  }
  for (const lvl of data.support ?? []) {
    pushRs(lvl, "S");
  }
  if (data.pivot && !consumedPivot.yes) {
    items.push({
      key: "PIVOT",
      kind: "PIVOT",
      label: "PIVOT",
      price: data.pivot.strike,
      pivot: data.pivot,
    });
  }
  if (data.max_pain) {
    items.push({
      key: "MAX PAIN",
      kind: "MAX_PAIN",
      label: "MAX PAIN",
      price: data.max_pain.strike,
      maxPain: data.max_pain,
    });
  }
  items.push({ key: "SPOT", kind: "SPOT", label: "SPOT", price: spot });
  items.sort((a, b) => b.price - a.price || a.key.localeCompare(b.key));
  return items;
}
