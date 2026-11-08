/**
 * Chart chrome helpers: Options must CSS-hide ChartView (not unmount it), and
 * Auto / scroll-to-live share one definition of the default visible window.
 */
import { initialRange, type LogicalRange } from "./view";

export type MainView = "chart" | "options";

/** Bars shown on first load / Auto reset (matches ChartView). */
export const INITIAL_VISIBLE_BARS = 150;
/** Lightweight Charts default bar spacing. */
export const DEFAULT_BAR_SPACING = 6;
/** Right-side air in bars (matches ChartView timeScale.rightOffset). */
export const DEFAULT_RIGHT_OFFSET = 5;

/**
 * Options replaces the Chart *visually* only. Unmounting ChartView drops the LWC
 * visible range and remounts into initialRange / a stale focus jump.
 */
export function chartHidden(mainView: MainView): boolean {
  return mainView === "options";
}

/** Default time window: last N bars plus right pad, scrolled to the live tip. */
export function defaultVisibleRange(barCount: number): LogicalRange {
  return initialRange(barCount, INITIAL_VISIBLE_BARS, DEFAULT_RIGHT_OFFSET);
}

/**
 * True when the user has scrolled left of the live tip (or the loaded window
 * dropped newer bars). The "»" button shows in that case.
 */
export function isAwayFromLive(
  range: LogicalRange | null,
  barCount: number,
  hasMoreNewer = false,
  slack = 2,
): boolean {
  if (hasMoreNewer) return true;
  if (!range || barCount <= 0) return false;
  return range.to < barCount - slack;
}

/** What Auto / price-axis double-click restore. */
export interface AutoReset {
  autoScale: true;
  barSpacing: number;
  rightOffset: number;
  range: LogicalRange;
}

export function autoReset(barCount: number): AutoReset {
  return {
    autoScale: true,
    barSpacing: DEFAULT_BAR_SPACING,
    rightOffset: DEFAULT_RIGHT_OFFSET,
    range: defaultVisibleRange(barCount),
  };
}
