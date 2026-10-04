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
  role: "target" | "stop" | "centre";
  text: string;
  lines: string[];
  /** Centre box is green when the result is not a loss. */
  tone: "profit" | "loss";
  tooltip?: string;
}

export interface BadgePlacement {
  role: "target" | "stop" | "centre";
  x: number;
  y: number;
  w: number;
  h: number;
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
  return `risk/lot Rs ${trimNum(riskPerLot)} > budget Rs ${trimNum(budget)}`;
}

const TICK = 0.05;

/** Two decimals, then drop a trailing zero the way TradingView prints 2.5 and 1. */
export function trimNum(value: number): string {
  return value.toFixed(2).replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "");
}

function amountText(points: number, lots: number, lotSize: number): string {
  if (lots > 0 && lotSize >= 1) return trimNum(points * lots * lotSize);
  if (lotSize >= 1) return `${trimNum(points * lotSize)} per lot`;
  return "0";
}

function zoneLine(name: "Target" | "Stop", points: number, percent: number, lots: number, lotSize: number): string {
  const ticks = points / TICK;
  return `${name}: ${trimNum(points)} (${trimNum(percent)}%) ${trimNum(ticks)}, Amount: ${amountText(points, lots, lotSize)}`;
}

export function optionCentreLine(note: string): string | null {
  const entry = note.match(/entry ([0-9]+(?:\.[0-9]+)?)/);
  const target = note.match(/target ([0-9]+(?:\.[0-9]+)?)/);
  const stop = note.match(/stop ([0-9]+(?:\.[0-9]+)?)/);
  const qty = note.match(/option-based quantity ([0-9]+) lots/);
  if (!entry?.[1] || !target?.[1] || !stop?.[1] || !qty?.[1]) return null;
  const kind = /\bPE\b/.test(note) ? "PE" : "CE";
  return `ATM ${kind} ~${trimNum(Number(entry[1]))} -> T ${trimNum(Number(target[1]))} / S ${trimNum(Number(stop[1]))}, option qty ${qty[1]}`;
}

export function positionZones(side: PositionSide, entry: number, target: number, stop: number): { profit: PriceSpan; risk: PriceSpan } {
  const band = (a: number, b: number): PriceSpan => ({ high: Math.max(a, b), low: Math.min(a, b) });
  if (side === "short") return { profit: band(entry, target), risk: band(stop, entry) };
  return { profit: band(entry, target), risk: band(entry, stop) };
}

function centreLines(levels: PositionLevels, size: PositionSize, outcome: PositionOutcome, compact: boolean, optionNote?: string | null): string[] {
  const qty = `Qty: ${size.lots}`;
  const ratio = levels.ratio == null ? null : `Risk/reward ratio: ${trimNum(levels.ratio)}`;
  if (compact) {
    const lines = [qty];
    if (ratio) lines.push(ratio);
    const note = quantityNote(size.lots, size.riskPerLot, size.riskBudget);
    if (note) lines.push(note);
    return lines;
  }
  const pnl = outcome.pnlRupees ?? 0;
  let head = `Open PnL: ${trimNum(pnl)}, ${qty}`;
  if (outcome.status === "pending") head = `Pending, ${qty}`;
  if (outcome.status === "not_entered") head = `Not entered, ${qty}`;
  if (outcome.status === "target" || outcome.status === "stop" || outcome.status === "ambiguous") head = `Closed PnL: ${trimNum(pnl)}, ${qty}`;
  const lines = [head];
  if (ratio) lines.push(ratio);
  const note = quantityNote(size.lots, size.riskPerLot, size.riskBudget);
  if (note) lines.push(note);
  if (outcome.status === "ambiguous") lines.push("stop assumed");
  if (optionNote && outcome.status !== "pending" && outcome.status !== "not_entered") {
    const option = optionCentreLine(optionNote);
    if (option) lines.push(option);
  }
  return lines;
}

export function positionLabels(
  levels: PositionLevels,
  size: PositionSize,
  outcome: PositionOutcome,
  compact: boolean,
  optionNote?: string | null,
): PositionLabel[] {
  const tone: PositionLabel["tone"] = outcome.pnlRupees != null && outcome.pnlRupees < 0 ? "loss" : "profit";
  const centre = centreLines(levels, size, outcome, compact, optionNote);
  const labels: PositionLabel[] = [
    { role: "centre", text: centre.join("\n"), lines: centre, tone, tooltip: optionNote ? "estimated" : undefined },
  ];
  if (compact) return labels;
  labels.unshift(
    { role: "target", text: zoneLine("Target", levels.rewardPoints, levels.rewardPercent, size.lots, size.lotSize), lines: [zoneLine("Target", levels.rewardPoints, levels.rewardPercent, size.lots, size.lotSize)], tone: "profit" },
    { role: "stop", text: zoneLine("Stop", levels.riskPoints, levels.riskPercent, size.lots, size.lotSize), lines: [zoneLine("Stop", levels.riskPoints, levels.riskPercent, size.lots, size.lotSize)], tone: "loss" },
  );
  return labels;
}

/** Keep a drag handle off the label it would otherwise cover. */
export function shiftHandleOffBadges(
  point: { x: number; y: number },
  badges: readonly { x: number; y: number; w: number; h: number }[],
  pad = 7,
): { x: number; y: number } {
  for (const badge of badges) {
    const inside = point.x >= badge.x && point.x <= badge.x + badge.w && point.y >= badge.y && point.y <= badge.y + badge.h;
    if (!inside) continue;
    const toLeft = point.x - badge.x;
    const toRight = badge.x + badge.w - point.x;
    return toLeft <= toRight ? { x: badge.x - pad, y: point.y } : { x: badge.x + badge.w + pad, y: point.y };
  }
  return point;
}

function rangesOverlap(a: BadgePlacement, b: BadgePlacement): boolean {
  return a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
}

/** Centre the badges on the tool and keep every box off the others. */
export function placePositionBadges(input: {
  toolLeft: number;
  toolWidth: number;
  entryY: number;
  profitTop: number;
  profitHeight: number;
  riskTop: number;
  riskHeight: number;
  badges: readonly { role: "target" | "stop" | "centre"; w: number; h: number }[];
  paneHeight: number;
}): BadgePlacement[] {
  const gap = 4;
  const mid = input.toolLeft + input.toolWidth / 2;
  const placed = new Map<BadgePlacement["role"], BadgePlacement>();
  const box = (role: BadgePlacement["role"], y: number): void => {
    const badge = input.badges.find((item) => item.role === role);
    if (!badge) return;
    placed.set(role, { role, x: mid - badge.w / 2, y, w: badge.w, h: badge.h });
  };
  const centre = input.badges.find((item) => item.role === "centre");
  if (centre) box("centre", input.entryY - centre.h / 2);
  const profitAbove = input.profitTop + input.profitHeight <= input.entryY + 1;
  const riskAbove = input.riskTop + input.riskHeight <= input.entryY + 1;
  const target = input.badges.find((item) => item.role === "target");
  if (target) box("target", profitAbove ? input.profitTop - gap - target.h : input.profitTop + input.profitHeight + gap);
  const stop = input.badges.find((item) => item.role === "stop");
  if (stop) box("stop", riskAbove ? input.riskTop - gap - stop.h : input.riskTop + input.riskHeight + gap);

  const pushAway = (role: "target" | "stop", above: boolean): void => {
    const outer = placed.get(role);
    const middle = placed.get("centre");
    if (!outer || !middle) return;
    if (!rangesOverlap(outer, middle)) return;
    outer.y = above ? middle.y - gap - outer.h : middle.y + middle.h + gap;
  };
  pushAway("target", profitAbove);
  pushAway("stop", riskAbove);

  const all = [...placed.values()];
  if (all.length === 0) return [];
  const minY = Math.min(...all.map((item) => item.y));
  if (minY < 2) for (const item of all) item.y += 2 - minY;
  const maxY = Math.max(...all.map((item) => item.y + item.h));
  if (maxY > input.paneHeight - 2) for (const item of all) item.y -= maxY - (input.paneHeight - 2);

  const shiftBeside = (lower: BadgePlacement, upper: BadgePlacement): void => {
    if (!rangesOverlap(lower, upper)) return;
    lower.x = upper.x + upper.w + gap;
    if (lower.x + lower.w > input.toolLeft + input.toolWidth + lower.w) lower.x = upper.x - gap - lower.w;
  };
  const middle = placed.get("centre");
  const targetBox = placed.get("target");
  const stopBox = placed.get("stop");
  if (middle && targetBox) shiftBeside(targetBox, middle);
  if (middle && stopBox) shiftBeside(stopBox, middle);
  if (targetBox && stopBox) shiftBeside(stopBox, targetBox);
  return [...placed.values()];
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
