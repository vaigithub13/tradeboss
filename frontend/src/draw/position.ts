/**
 * Long and short position maths. Anchors stay time and price.
 * The outcome stays pending until price trades the entry after the start time.
 */
import { barStart, defaultStyle, whitespaceTimes, type Anchor, type Drawing } from "./model";

export type PositionSide = "long" | "short";
export type RiskMode = "percent" | "rupees";
export type PriceMode = "price" | "points";
export type PositionHandle = "entry" | "target" | "stop" | "right";
export type PositionStatus = "pending" | "not_entered" | "open" | "target" | "stop" | "ambiguous";

export interface PositionSettings {
  accountSize: number;
  riskMode: RiskMode;
  riskPercent: number;
  riskRupees: number;
  lotSize: number | null;
  priceMode: PriceMode;
  profitColor: string;
  stopColor: string;
  compact: boolean;
  options: boolean;
}

export interface Bar {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
}

export interface PositionLevels {
  side: PositionSide;
  entry: number;
  target: number;
  stop: number;
  rewardPoints: number;
  riskPoints: number;
  rewardPercent: number;
  riskPercent: number;
  ratio: number | null;
}

export interface PositionSize {
  riskBudget: number;
  lotSize: number;
  lots: number;
  quantity: number;
  rewardRupees: number;
  riskRupees: number;
  riskPerLot: number;
}

export interface PositionOutcome {
  status: PositionStatus;
  endTime: number | null;
  exitPrice: number | null;
  pnlPoints: number | null;
  pnlRupees: number | null;
  activatedAt: number | null;
  targetTime: number | null;
  stopTime: number | null;
}

export interface PositionLabel {
  role: "target" | "stop" | "outcome" | "quantity" | "option";
  text: string;
}

export interface PriceSpan {
  high: number;
  low: number;
}

const EMPTY: PositionOutcome = {
  status: "pending",
  endTime: null,
  exitPrice: null,
  pnlPoints: null,
  pnlRupees: null,
  activatedAt: null,
  targetTime: null,
  stopTime: null,
};

export function defaultPositionSettings(): PositionSettings {
  return {
    accountSize: 1_000_000,
    riskMode: "percent",
    riskPercent: 1,
    riskRupees: 10_000,
    lotSize: null,
    priceMode: "price",
    profitColor: "#089981",
    stopColor: "#f23645",
    compact: false,
    options: false,
  };
}

export function positionToolLabel(tool: "long_position" | "short_position"): string {
  return tool === "long_position" ? "Long position" : "Short position";
}

export function isPositionTool(tool: string): tool is "long_position" | "short_position" {
  return tool === "long_position" || tool === "short_position";
}

export function defaultPositionAnchors(
  side: PositionSide,
  entry: Anchor,
  timeframe: string,
  holidays: readonly string[] = [],
): Anchor[] {
  const risk = entry.price * 0.01;
  const target = side === "long" ? entry.price + risk * 2 : entry.price - risk * 2;
  const stop = side === "long" ? entry.price - risk : entry.price + risk;
  let time = barStart(entry.time, timeframe);
  for (let step = 0; step < 15; step += 1) {
    const next = whitespaceTimes(time, timeframe, 1, holidays)[0];
    if (next == null) break;
    time = next;
  }
  return [entry, { time, price: target }, { time, price: stop }];
}

export function movePositionHandle(anchors: readonly Anchor[], handle: PositionHandle, point: Anchor): Anchor[] {
  const entry = anchors[0];
  const target = anchors[1];
  const stop = anchors[2];
  if (!entry || !target || !stop) return [...anchors];
  if (handle === "entry") return [point, target, stop];
  if (handle === "target") return [entry, { time: target.time, price: point.price }, stop];
  if (handle === "stop") return [entry, target, { time: stop.time, price: point.price }];
  return [entry, { time: point.time, price: target.price }, { time: point.time, price: stop.price }];
}

export function priceOf(side: PositionSide, entry: number, points: number, role: "target" | "stop"): number {
  const distance = Math.abs(points);
  if (role === "target") return side === "long" ? entry + distance : entry - distance;
  return side === "long" ? entry - distance : entry + distance;
}

export function pointsOf(side: PositionSide, entry: number, price: number, role: "target" | "stop"): number {
  if (role === "target") return side === "long" ? price - entry : entry - price;
  return side === "long" ? entry - price : price - entry;
}

export function positionLevels(side: PositionSide, anchors: readonly Anchor[]): PositionLevels {
  const entry = anchors[0]?.price ?? 0;
  const target = anchors[1]?.price ?? entry;
  const stop = anchors[2]?.price ?? entry;
  const rewardPoints = side === "long" ? target - entry : entry - target;
  const riskPoints = side === "long" ? entry - stop : stop - entry;
  const rewardPercent = entry === 0 ? 0 : (rewardPoints / entry) * 100;
  const riskPercent = entry === 0 ? 0 : (riskPoints / entry) * 100;
  const ratio = riskPoints > 0 ? rewardPoints / riskPoints : null;
  return { side, entry, target, stop, rewardPoints, riskPoints, rewardPercent, riskPercent, ratio };
}

export function positionSize(levels: PositionLevels, settings: PositionSettings, lotSize: number): PositionSize {
  const stored = settings.lotSize;
  const lot = stored != null && stored >= 1 ? stored : lotSize >= 1 ? lotSize : 0;
  const riskBudget = settings.riskMode === "rupees" ? settings.riskRupees : (settings.accountSize * settings.riskPercent) / 100;
  const riskPerLot = levels.riskPoints > 0 && lot >= 1 ? levels.riskPoints * lot : 0;
  const lots = riskPerLot > 0 && riskBudget > 0 ? Math.floor(riskBudget / riskPerLot) : 0;
  const quantity = lots * lot;
  return {
    riskBudget,
    lotSize: lot,
    lots,
    quantity,
    rewardRupees: levels.rewardPoints * quantity,
    riskRupees: levels.riskPoints * quantity,
    riskPerLot,
  };
}

export function quantityNote(lots: number, riskPerLot: number, budget: number): string | null {
  if (lots > 0 || !(riskPerLot > budget)) return null;
  return `0 lots: risk per lot Rs ${riskPerLot.toFixed(2)} exceeds budget Rs ${budget.toFixed(2)}`;
}

export function positionZones(side: PositionSide, entry: number, target: number, stop: number): { profit: PriceSpan; risk: PriceSpan } {
  const band = (a: number, b: number): PriceSpan => ({ high: Math.max(a, b), low: Math.min(a, b) });
  if (side === "short") return { profit: band(entry, target), risk: band(stop, entry) };
  return { profit: band(entry, target), risk: band(entry, stop) };
}

function money(value: number): string {
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}`;
}

function rupees(value: number): string {
  return `₹${value.toFixed(2)}`;
}

export function positionLabels(
  levels: PositionLevels,
  size: PositionSize,
  outcome: PositionOutcome,
  compact: boolean,
): PositionLabel[] {
  const ratio = levels.ratio == null ? "" : ` ${levels.ratio.toFixed(2)}R`;
  const targetMove = money(levels.side === "long" ? levels.rewardPoints : -levels.rewardPoints);
  const stopMove = money(levels.side === "long" ? -levels.riskPoints : levels.riskPoints);
  const targetPct = levels.side === "long" ? levels.rewardPercent : -levels.rewardPercent;
  const stopPct = levels.side === "long" ? -levels.riskPercent : levels.riskPercent;
  const target = compact
    ? `${levels.target.toFixed(2)}${ratio}`
    : `${levels.target.toFixed(2)} (${targetMove}, ${money(targetPct)}%)${ratio} ${rupees(size.rewardRupees)}`;
  const stop = compact
    ? `${levels.stop.toFixed(2)}`
    : `${levels.stop.toFixed(2)} (${stopMove}, ${money(stopPct)}%) ${rupees(size.riskRupees)}`;
  const labels: PositionLabel[] = [
    { role: "target", text: target },
    { role: "stop", text: stop },
  ];
  const note = quantityNote(size.lots, size.riskPerLot, size.riskBudget);
  if (note) labels.push({ role: "quantity", text: note });
  if (outcome.status === "open" && outcome.pnlRupees != null) labels.push({ role: "outcome", text: `open ${rupees(outcome.pnlRupees)}` });
  if (outcome.status === "target") labels.push({ role: "outcome", text: "target hit" });
  if (outcome.status === "stop") labels.push({ role: "outcome", text: "stop hit" });
  if (outcome.status === "ambiguous") labels.push({ role: "outcome", text: "ambiguous (stop assumed)" });
  if (outcome.status === "not_entered") labels.push({ role: "outcome", text: "not entered" });
  return labels;
}

function touches(bar: Bar, price: number): boolean {
  return bar.low <= price && price <= bar.high;
}

function hitsTarget(side: PositionSide, bar: Bar, target: number): boolean {
  return side === "long" ? bar.high >= target : bar.low <= target;
}

function hitsStop(side: PositionSide, bar: Bar, stop: number): boolean {
  return side === "long" ? bar.low <= stop : bar.high >= stop;
}

function finish(
  status: "target" | "stop" | "ambiguous",
  when: number,
  levels: PositionLevels,
  quantity: number,
  activatedAt: number,
): PositionOutcome {
  const stopFill = status !== "target";
  const exitPrice = stopFill ? levels.stop : levels.target;
  const pnlPoints = stopFill ? -levels.riskPoints : levels.rewardPoints;
  return {
    status,
    endTime: when,
    exitPrice,
    pnlPoints,
    pnlRupees: pnlPoints * quantity,
    activatedAt,
    targetTime: status === "target" ? when : null,
    stopTime: stopFill ? when : null,
  };
}

function exitOf(side: PositionSide, bar: Bar, target: number, stop: number): "target" | "stop" | "both" | null {
  const targetHit = hitsTarget(side, bar, target);
  const stopHit = hitsStop(side, bar, stop);
  if (targetHit && stopHit) return "both";
  if (targetHit) return "target";
  if (stopHit) return "stop";
  return null;
}

export function positionOutcome(
  side: PositionSide,
  anchors: readonly Anchor[],
  bars: readonly Bar[],
  minutes: readonly Bar[],
  asOf: number | null,
  quantity: number,
): PositionOutcome {
  const entry = anchors[0];
  const targetAnchor = anchors[1];
  const stopAnchor = anchors[2];
  if (!entry || !targetAnchor || !stopAnchor) return EMPTY;
  const levels = positionLevels(side, anchors);
  const right = targetAnchor.time;
  const visible = bars
    .filter((bar) => bar.time >= entry.time && bar.time <= right && (asOf == null || bar.time <= asOf))
    .slice()
    .sort((a, b) => a.time - b.time);
  const seenEdge = (asOf != null && asOf >= right) || visible.some((bar) => bar.time >= right);
  let activatedAt: number | null = null;

  const minutesIn = (bar: Bar, nextTime: number): Bar[] =>
    minutes
      .filter((minute) => minute.time >= entry.time && minute.time >= bar.time && minute.time < nextTime && (asOf == null || minute.time <= asOf))
      .slice()
      .sort((a, b) => a.time - b.time);

  for (let i = 0; i < visible.length; i += 1) {
    const bar = visible[i];
    if (!bar) continue;
    const next = visible[i + 1]?.time ?? right + 1;
    const slice = minutesIn(bar, next);
    if (!activatedAt) {
      if (slice.length > 0) {
        const hit = slice.find((minute) => touches(minute, entry.price));
        if (!hit) continue;
        activatedAt = hit.time;
        const from = slice.filter((minute) => minute.time >= hit.time);
        for (const minute of from) {
          const exit = exitOf(side, minute, levels.target, levels.stop);
          if (exit === "both") return finish("ambiguous", minute.time, levels, quantity, activatedAt);
          if (exit) return finish(exit, minute.time, levels, quantity, activatedAt);
        }
        continue;
      }
      if (!touches(bar, entry.price)) continue;
      activatedAt = bar.time;
      const exit = exitOf(side, bar, levels.target, levels.stop);
      if (exit === "both") return finish("ambiguous", bar.time, levels, quantity, activatedAt);
      if (exit) return finish(exit, bar.time, levels, quantity, activatedAt);
      continue;
    }
    if (slice.length > 0) {
      for (const minute of slice) {
        const exit = exitOf(side, minute, levels.target, levels.stop);
        if (exit === "both") return finish("ambiguous", minute.time, levels, quantity, activatedAt);
        if (exit) return finish(exit, minute.time, levels, quantity, activatedAt);
      }
      continue;
    }
    const exit = exitOf(side, bar, levels.target, levels.stop);
    if (exit === "both") return finish("ambiguous", bar.time, levels, quantity, activatedAt);
    if (exit) return finish(exit, bar.time, levels, quantity, activatedAt);
  }

  if (activatedAt == null) {
    return seenEdge ? { ...EMPTY, status: "not_entered", endTime: right } : EMPTY;
  }
  const last = visible[visible.length - 1];
  if (!last) return { ...EMPTY, status: "open", activatedAt };
  const pnlPoints = side === "long" ? last.close - entry.price : entry.price - last.close;
  return {
    status: "open",
    endTime: last.time,
    exitPrice: last.close,
    pnlPoints,
    pnlRupees: pnlPoints * quantity,
    activatedAt,
    targetTime: null,
    stopTime: null,
  };
}

export function createPositionDrawing(
  side: PositionSide,
  entry: Anchor,
  timeframe: string,
  knownAt: number,
  drawnOn: string,
  holidays: readonly string[] = [],
): Drawing {
  const tool = side === "long" ? "long_position" : "short_position";
  return {
    id: `${tool}-${entry.time}`,
    tool,
    anchors: defaultPositionAnchors(side, entry, timeframe, holidays),
    knownAt,
    drawnOn,
    showOn: null,
    hidden: false,
    locked: false,
    text: "",
    style: defaultStyle(tool),
    position: defaultPositionSettings(),
  };
}
