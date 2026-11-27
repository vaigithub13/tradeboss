import type { CanvasRenderingTarget2D } from "fancy-canvas";

import { planEdges, placePatternPill, signalPill, studyTip, zoneStudy } from "./chartLook";
import { sourceTags, strengthDots } from "./zones";
import type { WatchSignal } from "./signals";
import { nearPrice } from "./sanity";
import type { Study, StudyBox, StudyLabel, StudyLayers, StudyNote } from "./study";
import { recentLabels } from "./study";
import type { AxisTag } from "./tags";
import { istClock, istEpoch, learnAxisTime } from "./time";
import type {
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  SeriesAttachedParameter,
  Time,
  UTCTimestamp,
} from "lightweight-charts";

export interface LearnSegment {
  from: number;
  to: number;
  price: number;
  text: string;
  color: string;
  /** A short tag at the break, rather than a line across the whole swing. */
  short?: boolean;
}

export interface LearnMark {
  time: number;
  price: number;
  text: string;
  color: string;
  above: boolean;
}

export interface LearnBox {
  from: number;
  to: number;
  priceLow: number;
  priceHigh: number;
  color: string;
  text?: string;
}

export interface StackPoint {
  x: number;
  y: number;
  above: boolean;
  w: number;
}

/** Nudge labels that would share the same spot. Each step is 12px away from the candle. */
export function stackedYs(points: readonly StackPoint[]): number[] {
  const placed: { x: number; y: number; w: number }[] = [];
  const ys: number[] = [];
  for (const point of points) {
    let y = point.y;
    for (let step = 0; step < 8; step += 1) {
      const hit = placed.some((prev) => Math.abs(prev.x - point.x) < (prev.w + point.w) / 2 && Math.abs(prev.y - y) < 11);
      if (!hit) break;
      y += point.above ? -12 : 12;
    }
    placed.push({ x: point.x, y, w: point.w });
    ys.push(y);
  }
  return ys;
}

export interface ChartMark {
  id: string;
  kind: string;
  detail: string;
  time: number;
  tip?: string;
  low?: number;
  high?: number;
}

interface HitBox {
  x: number;
  y: number;
  w: number;
  h: number;
  mark: ChartMark;
}

interface Anchor {
  x: number;
  y: number;
  text: string;
  color: string;
  above: boolean;
  w: number;
  label?: StudyLabel;
}

/** Boxes, short BOS / CHoCH tags, and stacked swing labels. Font is 9px. */
export class LearnOverlay implements ISeriesPrimitive<Time> {
  segments: LearnSegment[] = [];
  marks: LearnMark[] = [];
  boxes: LearnBox[] = [];
  private study: Study | null = null;
  private layers: StudyLayers | null = null;
  private labelLimit = 12;
  private notes: StudyNote[] = [];
  private signals: WatchSignal[] = [];
  private events: { time: number; title: string; verified: boolean }[] = [];
  private focus = "";
  private selected: string | null = null;
  private tags: AxisTag[] = [];
  private showFaint = false;
  private countdown: string | null = null;
  hits: HitBox[] = [];
  private chart: IChartApi | null = null;
  private series: ISeriesApi<"Candlestick"> | null = null;
  private requestUpdate: () => void = () => {};
  private readonly views: readonly IPrimitivePaneView[];

  constructor() {
    const renderer: IPrimitivePaneRenderer = {
      draw: (target: CanvasRenderingTarget2D) => {
        target.useMediaCoordinateSpace(({ context, mediaSize }) => this.paint(context, mediaSize.width, mediaSize.height));
      },
    };
    this.views = [{ renderer: () => renderer, zOrder: () => "top" }];
  }

  attached(param: SeriesAttachedParameter<Time>): void {
    this.chart = param.chart;
    this.series = param.series as ISeriesApi<"Candlestick">;
    this.requestUpdate = param.requestUpdate;
  }

  detached(): void {
    this.chart = null;
    this.series = null;
  }

  updateAllViews(): void {}

  paneViews(): readonly IPrimitivePaneView[] {
    return this.views;
  }

  setScene(scene: { segments: LearnSegment[]; marks: LearnMark[]; boxes: LearnBox[] }): void {
    this.segments = scene.segments;
    this.marks = scene.marks;
    this.boxes = scene.boxes;
    this.requestUpdate();
  }

  setStudy(input: {
    study: Study;
    layers: StudyLayers;
    limit: number;
    notes: StudyNote[];
    selected: string | null;
    signals?: WatchSignal[];
    events?: { time: number; title: string; verified: boolean }[];
    focus?: string;
    tags?: AxisTag[];
    showFaint?: boolean;
    countdown?: string | null;
  }): void {
    this.study = input.study;
    this.layers = input.layers;
    this.labelLimit = input.limit;
    this.notes = input.notes;
    this.signals = input.signals ?? [];
    this.events = input.events ?? [];
    this.focus = input.focus ?? "";
    this.selected = input.selected;
    this.tags = input.tags ?? [];
    this.showFaint = input.showFaint ?? false;
    this.countdown = input.countdown ?? null;
    this.requestUpdate();
  }

  pick(x: number, y: number): ChartMark | null {
    const hits = this.hits.filter((hit) => x >= hit.x && x <= hit.x + hit.w && y >= hit.y && y <= hit.y + hit.h);
    hits.sort((a, b) => a.w * a.h - b.w * b.h);
    return hits[0]?.mark ?? null;
  }

  private paint(ctx: CanvasRenderingContext2D, width = 0, height = 0): void {
    if (this.study && this.layers) {
      this.paintStudy(ctx, width, height);
      return;
    }
    const chart = this.chart;
    const series = this.series;
    if (!chart || !series) return;
    ctx.save();
    ctx.font = "9px sans-serif";
    ctx.lineWidth = 1;
    const anchors: Anchor[] = [];
    for (const box of this.boxes) {
      const x1 = chart.timeScale().timeToCoordinate(box.from as UTCTimestamp);
      const x2 = chart.timeScale().timeToCoordinate(box.to as UTCTimestamp);
      const y1 = series.priceToCoordinate(box.priceHigh);
      const y2 = series.priceToCoordinate(box.priceLow);
      if (x1 == null || x2 == null || y1 == null || y2 == null) continue;
      const left = Math.min(x1, x2);
      const right = Math.max(x1, x2, left + 8);
      const top = Math.min(y1, y2);
      const bottom = Math.max(y1, y2, top + 1);
      ctx.fillStyle = box.color;
      ctx.fillRect(left, top, right - left, bottom - top);
      if (box.text) anchors.push({ x: left + 4, y: top + 10, text: box.text, color: "#1c2330", above: true, w: box.text.length * 5.2 });
    }
    for (const seg of this.segments) {
      const x1 = chart.timeScale().timeToCoordinate(seg.from as UTCTimestamp);
      const x2 = chart.timeScale().timeToCoordinate(seg.to as UTCTimestamp);
      const y = series.priceToCoordinate(seg.price);
      if (x1 == null || x2 == null || y == null) continue;
      const end = Math.max(x1, x2);
      const start = seg.short ? end - 32 : Math.min(x1, x2);
      ctx.strokeStyle = seg.color;
      ctx.beginPath();
      ctx.moveTo(start, y);
      ctx.lineTo(end, y);
      ctx.stroke();
      anchors.push({ x: end + 3, y: y - 3, text: seg.text, color: seg.color, above: true, w: seg.text.length * 5.2 });
    }
    for (const mark of this.marks) {
      const x = chart.timeScale().timeToCoordinate(mark.time as UTCTimestamp);
      const y = series.priceToCoordinate(mark.price);
      if (x == null || y == null) continue;
      drawArrow(ctx, x, y, mark.above, mark.color);
      anchors.push({
        x: x + 6,
        y: mark.above ? y - 8 : y + 10,
        text: mark.text,
        color: mark.color,
        above: mark.above,
        w: mark.text.length * 5.2,
      });
    }
    const ys = stackedYs(anchors);
    for (let i = 0; i < anchors.length; i += 1) {
      const anchor = anchors[i]!;
      ctx.fillStyle = anchor.color;
      ctx.fillText(anchor.text, anchor.x, ys[i]!);
    }
    ctx.restore();
  }

  private paintStudy(ctx: CanvasRenderingContext2D, width: number, height: number): void {
    const chart = this.chart;
    const series = this.series;
    const study = this.study;
    const layers = this.layers;
    if (!chart || !series || !study || !layers) return;
    const hits: HitBox[] = [];
    const xOf = (time: number) => chart.timeScale().timeToCoordinate(time as UTCTimestamp);
    const yOf = (price: number) => series.priceToCoordinate(price);
    ctx.save();
    ctx.lineWidth = 1;
    if (layers.session) {
      for (const session of study.sessions) {
        const x1 = xOf(session.t1);
        const x2 = xOf(session.t2);
        if (x1 == null || x2 == null) continue;
        const left = Math.min(x1, x2);
        const w = Math.max(Math.abs(x2 - x1), 2);
        const y = height - (layers.killzones ? 36 : 12);
        ctx.fillStyle = session.text;
        ctx.globalAlpha = 0.55;
        ctx.fillRect(left, y, w, 10);
        ctx.globalAlpha = 1;
        ctx.fillStyle = "#ffffff";
        ctx.font = "600 9px sans-serif";
        const short = session.name === "Opening hour" ? "Open" : session.name === "Midday" ? "Mid" : "Close";
        if (w > 28) ctx.fillText(short, left + 3, y + 8);
        hits.push({ x: left, y, w, h: 10, mark: { id: `ses-${session.t1}`, kind: "session", detail: session.name, time: session.t1 } });
      }
    }
    if (layers.killzones) {
      for (const zone of study.killzones ?? []) {
        const x1 = xOf(zone.t1);
        const x2 = xOf(zone.t2);
        if (x1 == null || x2 == null) continue;
        const left = Math.min(x1, x2);
        const w = Math.max(Math.abs(x2 - x1), 2);
        const silver = zone.kind === "sb";
        const y = silver ? height - 12 : height - 24;
        const on = !this.focus || this.focus === zone.kind;
        ctx.globalAlpha = on ? 1 : 0.25;
        ctx.fillStyle = silver ? "rgba(234,88,12,0.55)" : "rgba(30,64,175,0.30)";
        ctx.fillRect(left, y, w, 10);
        ctx.fillStyle = silver ? "#fff" : "#1e3a8a";
        ctx.font = "600 9px sans-serif";
        ctx.fillText(zone.name, left + 3, y + 8);
        ctx.globalAlpha = 1;
        hits.push({ x: left, y, w, h: 10, mark: { id: `kz-${zone.t1}-${zone.name}`, kind: zone.kind, detail: zone.detail, time: zone.t1 } });
      }
      const days = [...new Set(study.sessions.map((session) => istClock(session.t1).day))];
      ctx.font = "600 9px sans-serif";
      for (const day of days) {
        const [year, month, date] = day.split("-").map(Number);
        const closeAt = istEpoch(year ?? 1970, month ?? 1, date ?? 1, 15, 30);
        const x = xOf(closeAt);
        if (x == null) continue;
        ctx.fillStyle = "#b91c1c";
        ctx.fillRect(x, height - 10, 2, 10);
        ctx.fillText("Market close", x + 4, height - 2);
        hits.push({
          x, y: height - 12, w: 70, h: 12,
          mark: { id: `close-${day}`, kind: "session", detail: `Market close 15:30 IST ${day}`, time: closeAt },
        });
      }
    }
    const boxes = study.boxes.filter((box) => showBox(box, layers, study));
    const wide = new Set(bandBoxes(boxes, study.last).map((box) => box.id));
    for (const box of boxes) {
      const y1 = yOf(box.p1);
      const y2 = yOf(box.p2);
      if (y1 == null || y2 == null) continue;
      const top = Math.min(y1, y2);
      const band = Math.max(Math.abs(y2 - y1), 3);
      const hot = !this.focus || focused(box.kind, this.focus);
      const full = wide.has(box.id);
      const x1 = full ? 0 : xOf(box.t1);
      const x2 = full ? width : xOf(box.t2);
      if (x1 == null || x2 == null) continue;
      const left = Math.min(x1, x2);
      const w = Math.max(Math.abs(x2 - x1), 6);
      ctx.globalAlpha = hot ? (full ? 0.9 : 1) : 0.2;
      ctx.fillStyle = box.color;
      ctx.fillRect(left, top, w, band);
      ctx.strokeStyle = box.border;
      ctx.lineWidth = 1;
      if (!full) ctx.strokeRect(left, top, w, band);
      ctx.globalAlpha = 1;
      if (box.guide != null) {
        const ys = yOf(box.guide);
        if (ys != null) {
          ctx.strokeStyle = "#2563eb";
          ctx.setLineDash([2, 2]);
          ctx.beginPath();
          ctx.moveTo(left, ys);
          ctx.lineTo(left + w, ys);
          ctx.stroke();
          ctx.setLineDash([]);
          ctx.font = "600 9px sans-serif";
          ctx.fillStyle = "#2563eb";
          ctx.fillText("70.5%", left + w - 36, ys - 2);
        }
      }
      if (box.label) {
        ctx.font = "600 10px sans-serif";
        ctx.fillStyle = box.labelColor;
        ctx.fillText(box.label.slice(0, 16), left + 4, top + 11);
        const prices = `${Math.round(Math.min(box.p1, box.p2)).toLocaleString("en-IN")}–${Math.round(Math.max(box.p1, box.p2)).toLocaleString("en-IN")}`;
        ctx.fillText(prices, Math.max(4, left + w - prices.length * 6), top + 11);
      }
      hits.push({ x: left, y: top, w, h: band, mark: { id: box.id, kind: box.kind, detail: box.detail, time: box.confirm } });
    }
    const lines = study.lines.filter((line) => showLine(line, layers) && !LIQUIDITY_KIND.has(line.kind));
    for (const line of lines) {
      const y = yOf(line.price);
      if (y == null) continue;
      const x1 = xOf(line.t1);
      const x2 = xOf(line.t2);
      const structure = line.kind === "bos" || line.kind === "choch";
      if (line.kind !== "eq" && (x1 == null || x2 == null)) continue;
      const end = line.kind === "eq" ? width : Math.max(x1 ?? 0, x2 ?? 0);
      const start = line.kind === "eq" ? 0 : line.short ? end - 32 : Math.min(x1 ?? 0, x2 ?? 0);
      ctx.strokeStyle = line.color;
      ctx.lineWidth = structure ? 1 : 1;
      ctx.setLineDash(line.dashed || line.kind === "eq" ? [4, 3] : []);
      ctx.beginPath();
      ctx.moveTo(start, y);
      ctx.lineTo(Math.max(end, start + 8), y);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.font = structure ? "600 9px sans-serif" : "600 10px sans-serif";
      ctx.fillStyle = line.color;
      const lineText = line.kind === "eq" ? "EQ 50%" : line.label.slice(0, 12);
      ctx.fillText(lineText, line.kind === "eq" ? width - 52 : end + 4, y - 3);
      hits.push({
        x: start, y: y - 6, w: Math.max(end - start, 8), h: 12,
        mark: { id: line.id, kind: line.kind, detail: line.detail, time: line.confirm, tip: line.tip },
      });
    }
    if (layers.round) {
      ctx.setLineDash([2, 4]);
      for (const mark of study.rounds) {
        const y = yOf(mark.price);
        if (y == null) continue;
        ctx.strokeStyle = mark.major ? "rgba(100,116,139,0.45)" : "rgba(100,116,139,0.2)";
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(width, y);
        ctx.stroke();
        hits.push({
          x: 0, y: y - 4, w: width, h: 8,
          mark: { id: `round-${mark.price}`, kind: "round", detail: `${mark.price}${mark.major ? " (100 points)" : ""}`, time: study.sessions[0]?.t1 ?? 0 },
        });
      }
      ctx.setLineDash([]);
    }
    const labels = recentLabels(study.labels.filter((label) => showLabel(label, layers)), this.labelLimit);
    const anchors: Anchor[] = labels.map((label) => {
      const x = xOf(label.time);
      const y = yOf(label.price);
      return {
        x: x ?? Number.NaN,
        y: y ?? Number.NaN,
        text: label.text,
        color: label.color,
        above: label.pos === "above",
        w: label.text.length * 7 + 8,
        label,
      };
    }).filter((anchor) => Number.isFinite(anchor.x) && Number.isFinite(anchor.y));
    const ys = stackPills(anchors);
    for (let i = 0; i < anchors.length; i += 1) {
      const anchor = anchors[i]!;
      const label = anchor.label!;
      const pattern = label.kind.startsWith("engulf") || label.kind.startsWith("pin") || label.kind === "doji" || label.kind === "inside";
      const swing = label.kind === "hh" || label.kind === "hl" || label.kind === "lh" || label.kind === "ll" || label.kind === "swing_high" || label.kind === "swing_low";
      const upSwing = label.kind === "hh" || label.kind === "hl" || label.kind === "swing_low";
      const placed = pattern
        ? placePatternPill(anchor.x, anchor.above ? anchor.y : anchor.y - 10, anchor.above ? anchor.y + 10 : anchor.y, anchor.above)
        : null;
      const y = placed ? placed.y : (ys[i] ?? anchor.y);
      const x = placed ? placed.x : anchor.x;
      const w = placed ? placed.w : swing ? anchor.text.length * 5.4 + 8 : anchor.w;
      const h = swing ? 13 : 15;
      const top = placed ? y : anchor.above ? y - h : y;
      ctx.font = swing || pattern ? "600 9px sans-serif" : "600 11px sans-serif";
      ctx.fillStyle = swing ? (upSwing ? "#dcfce7" : "#fee2e2") : label.color;
      roundRect(ctx, x, top, w, h, 4);
      ctx.fill();
      ctx.fillStyle = swing ? (upSwing ? "#166534" : "#991b1b") : label.textColor;
      ctx.fillText(label.text, x + 4, top + (swing ? 10 : 11));
      if (this.selected === label.id) {
        ctx.strokeStyle = "#b45309";
        ctx.strokeRect(x - 1, top - 1, w + 2, h + 2);
      }
      hits.push({ x, y: top, w, h, mark: { id: label.id, kind: label.kind, detail: label.detail, time: label.time, tip: label.tip } });
    }
    if (layers.events) {
      ctx.font = "600 10px sans-serif";
      for (const mark of this.events) {
        const x = xOf(mark.time);
        if (x == null) continue;
        ctx.fillStyle = mark.verified ? "#1d4ed8" : "#b45309";
        ctx.fillRect(x, 18, 2, 12);
        ctx.fillText(mark.title.slice(0, 12), x + 4, 28);
        hits.push({
          x, y: 16, w: 120, h: 16,
          mark: { id: `event-${mark.time}-${mark.title}`, kind: "event", detail: `${mark.title}${mark.verified ? "" : " (unverified)"}`, time: mark.time },
        });
      }
    }
    if (layers.signals) {
      for (const signal of this.signals) {
        const x1 = xOf(signal.time);
        const yEntry = yOf(signal.entry);
        if (x1 == null || yEntry == null) continue;
        const reason = `${signal.setup} ${signal.side.toUpperCase()} ${signal.status}. ${signal.why}`;
        const detail = `${reason}. SL ${signal.sl}, TP ${signal.tp} (${signal.tpNote}).`;
        const tip = studyTip(signal.side, signal.entry, signal.sl, signal.tp);
        const today = istClock(signal.time).day === study.day;
        if (!today) {
          ctx.fillStyle = signal.side === "buy" ? "#16a34a" : "#dc2626";
          ctx.beginPath();
          ctx.arc(x1, yEntry, 3, 0, Math.PI * 2);
          ctx.fill();
          hits.push({ x: x1 - 4, y: yEntry - 4, w: 8, h: 8, mark: { id: signal.id, kind: "signal", detail, time: signal.time, tip } });
          continue;
        }
        const endAt = signal.exitTime ?? signal.time + 15 * 60;
        const x2 = xOf(endAt) ?? x1 + 36;
        const ySl = yOf(signal.sl);
        const yTp = yOf(signal.tp);
        const buy = signal.side === "buy";
        if (ySl != null && yTp != null) {
          const left = Math.min(x1, x2);
          const top = Math.min(ySl, yTp);
          const w = Math.max(Math.abs(x2 - x1), 28);
          const h = Math.max(Math.abs(yTp - ySl), 8);
          ctx.fillStyle = signal.status === "TP"
            ? "rgba(22,163,74,0.12)"
            : signal.status === "SL"
              ? "rgba(220,38,38,0.12)"
              : "rgba(100,116,139,0.08)";
          ctx.fillRect(left, top, w, h);
        }
        const between = study.last >= Math.min(signal.sl, signal.tp) && study.last <= Math.max(signal.sl, signal.tp);
        const text = signalPill(signal.side, signal.status, learnAxisTime(signal.time), signal.status === "open" && between);
        const py = signal.side === "buy" ? yEntry + 8 : yEntry - 22;
        pill(ctx, x1 + 8, py, text, buy ? "#16a34a" : "#dc2626", "#ffffff");
        hits.push({
          x: x1 + 8, y: py, w: text.length * 6.2 + 8, h: 14,
          mark: { id: signal.id, kind: "signal", detail, time: signal.time, tip, low: Math.min(signal.sl, signal.tp), high: Math.max(signal.sl, signal.tp) },
        });
      }
    }
    if (layers.notes) {
      for (const note of this.notes) {
        const x = xOf(note.time);
        const y = yOf(note.price);
        if (x == null || y == null) continue;
        ctx.fillStyle = "#b45309";
        ctx.beginPath();
        ctx.arc(x, y, 4, 0, Math.PI * 2);
        ctx.fill();
        ctx.font = "600 11px sans-serif";
        ctx.fillText(note.text, x + 8, y - 4);
        hits.push({ x: x - 4, y: y - 8, w: 80, h: 16, mark: { id: note.id, kind: "note", detail: note.text, time: note.time } });
      }
    }
    if (layers.zones) {
      const drawn = study.zones
        .filter((item) => item.visible || (item.faint && this.showFaint))
        .sort((a, b) => b.high - a.high);
      for (const zone of drawn) {
        const y1 = yOf(zone.high);
        const y2 = yOf(zone.low);
        if (y1 == null || y2 == null) continue;
        const top = Math.min(y1, y2);
        const band = Math.max(Math.abs(y2 - y1), 3);
        const ink = zone.role === "resistance" ? "#dc2626" : zone.role === "support" ? "#16a34a" : "#6b7280";
        const fill = zone.role === "resistance" ? "rgba(220,38,38,0.12)" : zone.role === "support" ? "rgba(22,163,74,0.12)" : "rgba(107,114,128,0.16)";
        ctx.globalAlpha = zone.faint ? 0.45 : 1;
        ctx.fillStyle = fill;
        ctx.fillRect(0, top, width, band);
        ctx.strokeStyle = ink;
        ctx.strokeRect(0, top, width, band);
        ctx.font = "600 10px sans-serif";
        ctx.fillStyle = ink;
        const side = zone.role === "resistance" ? "R" : zone.role === "support" ? "S" : "In";
        const caption = zone.role === "inside"
          ? "price inside zone"
          : `${side} · ${sourceTags(zone.sources)} ${strengthDots(zone.strength)}`;
        ctx.fillText(caption, 6, top + Math.min(12, band - 1));
        const prices = `${Math.round(zone.low).toLocaleString("en-IN")}–${Math.round(zone.high).toLocaleString("en-IN")}`;
        ctx.fillText(prices, width - prices.length * 6 - 8, top + Math.min(12, band - 1));
        ctx.globalAlpha = 1;
        const reading = zoneStudy(zone, study.last);
        hits.push({
          x: 0, y: top, w: width, h: band,
          mark: {
            id: zone.id, kind: "zone", detail: zone.detail, time: zone.lastTest ?? 0,
            tip: studyTip(reading.tone, reading.entry, reading.sl, reading.tp),
            low: zone.low, high: zone.high,
          },
        });
      }
      let topSlot = 0;
      let bottomSlot = 0;
      for (const plan of planEdges(study.zones, study.last)) {
        const y = plan.above ? 6 + topSlot++ * 18 : height - 52 - bottomSlot++ * 18;
        const w = plan.text.length * 6.2 + 8;
        pill(ctx, width - w - 8, y, plan.text, plan.side === "sell" ? "#dc2626" : "#16a34a", "#ffffff");
        hits.push({
          x: width - w - 8, y, w, h: 14,
          mark: {
            id: plan.id, kind: "plan", detail: plan.detail, time: 0,
            tip: plan.tip, low: plan.low, high: plan.high,
          },
        });
      }
    }
    if (this.countdown) {
      const y = yOf(study.last);
      if (y != null) pill(ctx, width - this.countdown.length * 6.2 - 14, y + 10, this.countdown, "#b45309", "#ffffff");
    }
    let tagY = -999;
    for (const tag of [...this.tags].sort((a, b) => b.price - a.price)) {
      const yRaw = yOf(tag.price);
      if (yRaw == null) continue;
      const y = Math.abs(yRaw - tagY) < 14 ? tagY + 14 : yRaw;
      tagY = y;
      const text = tag.text.slice(0, 16);
      const w = text.length * 6.2 + 8;
      const red = tag.kinds.includes("pd") && /PDH|PWH|OR high/.test(tag.text);
      const green = tag.kinds.includes("pd") && /PDL|PWL|OR low/.test(tag.text);
      const color = red ? "#dc2626" : green ? "#16a34a" : "#7c3aed";
      pill(ctx, width - w - 6, y - 8, text, color, "#ffffff");
      hits.push({
        x: width - w - 6, y: y - 8, w, h: 15,
        mark: { id: `tag-${tag.price}`, kind: "axis", detail: tag.detail, time: 0 },
      });
    }
    this.hits = hits;
    ctx.restore();
  }
}

const LIQUIDITY_KIND = new Set(["pdh", "pdl", "pwh", "pwl", "or_h", "or_l", "eqh", "eql"]);

/** Nearest unfilled gap or block on each side, plus the ICT overlap and the OTE band. */
function bandBoxes(boxes: readonly StudyBox[], last: number): StudyBox[] {
  const pool = boxes.filter((box) => box.kind.startsWith("fvg") || box.kind.startsWith("ob"));
  const mid = (box: StudyBox) => (box.p1 + box.p2) / 2;
  const above = pool.filter((box) => Math.min(box.p1, box.p2) >= last).sort((a, b) => mid(a) - mid(b))[0];
  const below = pool.filter((box) => Math.max(box.p1, box.p2) <= last).sort((a, b) => mid(b) - mid(a))[0];
  return [
    ...(above ? [above] : []),
    ...(below ? [below] : []),
    ...boxes.filter((box) => box.kind.startsWith("ict") || box.kind === "ote"),
  ];
}

function pill(ctx: CanvasRenderingContext2D, x: number, y: number, text: string, fill: string, ink: string): void {
  ctx.font = "600 10px sans-serif";
  const w = text.length * 6.2 + 8;
  ctx.fillStyle = fill;
  roundRect(ctx, x, y, w, 14, 4);
  ctx.fill();
  ctx.fillStyle = ink;
  ctx.fillText(text, x + 4, y + 11);
}

function finished(state: string | undefined): boolean {
  return state === "filled" || state === "old" || state === "filled-small" || state === "done" || state === "broken";
}

function showBox(box: { kind: string; state?: string; p1: number; p2: number }, layers: StudyLayers, study: Study): boolean {
  const faded = finished(box.state);
  const close = box.kind.startsWith("fvg") || box.kind.startsWith("ob") || box.kind.startsWith("ict") || box.kind.startsWith("brk") || box.kind.startsWith("uni");
  const nearby = !close || (faded && layers.fvgFilled) || nearPrice(Math.min(box.p1, box.p2), Math.max(box.p1, box.p2), study.last, study.atr);
  if (box.kind.startsWith("fvg")) return layers.fvg && nearby && (!faded || layers.fvgFilled);
  if (box.kind.startsWith("ob")) return layers.ob && box.state !== "broken" && nearby && (!faded || layers.fvgFilled);
  if (box.kind.startsWith("amd")) return layers.amd && (!faded || layers.fvgFilled);
  if (box.kind.startsWith("ict")) return layers.ict && nearby && (!faded || layers.fvgFilled);
  if (box.kind.startsWith("brk")) return layers.breaker && nearby && (!faded || layers.fvgFilled);
  if (box.kind.startsWith("uni")) return layers.unicorn && nearby && (!faded || layers.fvgFilled);
  if (box.kind === "ote" || box.kind === "premium" || box.kind === "discount") return layers.ote && (!faded || layers.fvgFilled);
  if (box.kind === "ndog" || box.kind === "nwog") return layers.gaps && (!faded || layers.fvgFilled);
  return true;
}

function showLine(line: { kind: string }, layers: StudyLayers): boolean {
  if (line.kind === "eq") return layers.ote;
  if (line.kind.startsWith("crt")) return layers.crt;
  if (line.kind === "bos" || line.kind === "choch") return layers.structure;
  return layers.liquidity;
}

function focused(kind: string, focus: string): boolean {
  if (!focus) return true;
  if (focus === "bos") return kind === "bos" || kind === "choch";
  if (focus === "liq") return kind === "sweep" || kind.startsWith("pd") || kind.startsWith("pw") || kind === "eqh" || kind === "eql" || kind.startsWith("or_");
  if (focus === "ndog") return kind === "ndog" || kind === "nwog";
  if (focus === "eq") return kind === "eq" || kind === "premium" || kind === "discount" || kind === "ote";
  return kind.startsWith(focus);
}

function showLabel(label: { kind: string; key?: boolean }, layers: StudyLayers): boolean {
  if (label.kind === "mss") return layers.structure;
  if (label.kind.startsWith("crt")) return layers.crt;
  if (label.kind === "sweep") return layers.liquidity;
  if (label.kind === "amd_m") return layers.amd;
  if (label.kind === "hh" || label.kind === "hl" || label.kind === "lh" || label.kind === "ll" || label.kind === "swing_high" || label.kind === "swing_low") {
    return layers.swing;
  }
  if (label.kind === "engulf_bull" || label.kind === "engulf_bear" || label.kind === "pin_bull" || label.kind === "pin_bear" || label.kind === "doji" || label.kind === "inside") {
    return layers.pattern && (label.key === true || layers.patternAll);
  }
  return false;
}

function stackPills(points: readonly Anchor[]): number[] {
  const placed: { x: number; y: number; w: number }[] = [];
  const ys: number[] = [];
  for (const point of points) {
    let y = point.above ? point.y - 16 : point.y + 4;
    for (let step = 0; step < 8; step += 1) {
      const hit = placed.some((prev) => Math.abs(prev.x - point.x) < (prev.w + point.w) / 2 && Math.abs(prev.y - y) < 16);
      if (!hit) break;
      y += point.above ? -17 : 17;
    }
    placed.push({ x: point.x, y, w: point.w });
    ys.push(y);
  }
  return ys;
}

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number): void {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function drawArrow(ctx: CanvasRenderingContext2D, x: number, y: number, above: boolean, color: string): void {
  const tip = above ? y - 6 : y + 6;
  const base = above ? y - 1 : y + 1;
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.moveTo(x, tip);
  ctx.lineTo(x - 3.5, base);
  ctx.lineTo(x + 3.5, base);
  ctx.closePath();
  ctx.fill();
}
