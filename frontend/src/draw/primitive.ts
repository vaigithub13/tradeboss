import type { CanvasRenderingTarget2D } from "fancy-canvas";
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

import type { FvgBox } from "./api";
import {
  fibPrices,
  mapAnchor,
  measure,
  shownDrawings,
  type Anchor,
  type Drawing,
  type DrawStyle,
} from "./model";

export interface DrawScene {
  drawings: readonly Drawing[];
  timeframe: string;
  cursor: number | null;
  hideAll: boolean;
  selectedId: string | null;
  draft: Anchor | null;
  hover: Anchor | null;
  tool: string;
}

interface Seg {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

interface Shape {
  id: string;
  style: DrawStyle;
  segments: Seg[];
  handles: { x: number; y: number; index: number }[];
  fill: { x: number; y: number; w: number; h: number } | null;
  labels: { x: number; y: number; text: string }[];
}

export interface DrawHit {
  id: string;
  handle: number | null;
}

type Chart = IChartApi;
type Series = ISeriesApi<"Candlestick">;

const HIT = 6;

function distToSeg(px: number, py: number, s: Seg): number {
  const dx = s.x2 - s.x1;
  const dy = s.y2 - s.y1;
  const len2 = dx * dx + dy * dy;
  if (len2 === 0) return Math.hypot(px - s.x1, py - s.y1);
  const t = Math.max(0, Math.min(1, ((px - s.x1) * dx + (py - s.y1) * dy) / len2));
  return Math.hypot(px - (s.x1 + t * dx), py - (s.y1 + t * dy));
}

function dash(style: DrawStyle["lineStyle"]): number[] {
  if (style === "dashed") return [6, 4];
  if (style === "dotted") return [1.5, 3];
  return [];
}

class DrawRenderer implements IPrimitivePaneRenderer {
  constructor(private readonly owner: DrawPrimitive) {}

  draw(target: CanvasRenderingTarget2D): void {
    target.useMediaCoordinateSpace(({ context, mediaSize }) => {
      const shapes = this.owner.shapes(mediaSize.width, mediaSize.height);
      for (const shape of shapes) {
        const ctx = context;
        ctx.save();
        ctx.lineWidth = Math.max(1, shape.style.width);
        ctx.strokeStyle = shape.style.color;
        ctx.fillStyle = shape.style.color;
        ctx.setLineDash(dash(shape.style.lineStyle));
        if (shape.fill && shape.style.fill) {
          ctx.save();
          ctx.globalAlpha = 0.16;
          ctx.fillStyle = shape.style.fill;
          ctx.fillRect(shape.fill.x, shape.fill.y, shape.fill.w, shape.fill.h);
          ctx.restore();
        }
        ctx.beginPath();
        for (const seg of shape.segments) {
          ctx.moveTo(seg.x1, seg.y1);
          ctx.lineTo(seg.x2, seg.y2);
        }
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.font = "11px sans-serif";
        ctx.textBaseline = "bottom";
        for (const label of shape.labels) ctx.fillText(label.text, label.x, label.y);
        if (shape.id === this.owner.scene.selectedId) {
          ctx.fillStyle = "#0b0e14";
          ctx.strokeStyle = "#ffffff";
          ctx.lineWidth = 1;
          for (const handle of shape.handles) {
            ctx.fillRect(handle.x - 4, handle.y - 4, 8, 8);
            ctx.strokeRect(handle.x - 4, handle.y - 4, 8, 8);
          }
        }
        ctx.restore();
      }
    });
  }
}

export class DrawPrimitive implements ISeriesPrimitive {
  scene: DrawScene = {
    drawings: [],
    timeframe: "15m",
    cursor: null,
    hideAll: false,
    selectedId: null,
    draft: null,
    hover: null,
    tool: "cursor",
  };

  private chart: Chart | null = null;
  private series: Series | null = null;
  private requestUpdate: () => void = () => {};
  private readonly renderer = new DrawRenderer(this);
  private readonly views: readonly IPrimitivePaneView[] = [{ renderer: () => this.renderer }];

  attached(param: SeriesAttachedParameter<Time, "Candlestick">): void {
    this.chart = param.chart as Chart;
    this.series = param.series as Series;
    this.requestUpdate = param.requestUpdate;
  }

  detached(): void {
    this.chart = null;
    this.series = null;
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return this.views;
  }

  updateAllViews(): void {}

  setScene(scene: DrawScene): void {
    this.scene = scene;
    this.requestUpdate();
  }

  pick(x: number, y: number): DrawHit | null {
    const size = this.chart?.paneSize();
    const shapes = this.shapes(size?.width ?? 0, size?.height ?? 0);
    const selected = shapes.find((shape) => shape.id === this.scene.selectedId);
    if (selected) {
      for (const handle of selected.handles) {
        if (Math.hypot(x - handle.x, y - handle.y) <= HIT + 2) return { id: selected.id, handle: handle.index };
      }
    }
    let best: { id: string; dist: number } | null = null;
    for (const shape of shapes) {
      if (shape.id === "draft") continue;
      for (const seg of shape.segments) {
        const dist = distToSeg(x, y, seg);
        if (dist <= HIT && (best === null || dist < best.dist)) best = { id: shape.id, dist };
      }
      if (shape.fill) {
        const { x: fx, y: fy, w, h } = shape.fill;
        if (x >= fx && x <= fx + w && y >= fy && y <= fy + h && (best === null || 0 < best.dist)) {
          best = { id: shape.id, dist: 0 };
        }
      }
    }
    return best ? { id: best.id, handle: null } : null;
  }

  hitTest(x: number, y: number) {
    const hit = this.pick(x, y);
    if (!hit) return null;
    return { externalId: hit.id, zOrder: "normal" as const, cursorStyle: "pointer" };
  }

  shapes(width: number, height: number): Shape[] {
    const visible = shownDrawings(this.scene.drawings, this.scene.cursor, this.scene.hideAll);
    const ghost = this.ghost();
    const list = ghost ? [...visible, ghost] : visible;
    return list.flatMap((drawing) => {
      const shape = this.shapeFor(drawing, width, height);
      return shape ? [shape] : [];
    });
  }

  private ghost(): Drawing | null {
    const { draft, hover, tool } = this.scene;
    if (!draft || !hover || tool === "cursor" || tool === "eraser") return null;
    return {
      id: "draft",
      tool: tool as Drawing["tool"],
      anchors: [draft, hover],
      knownAt: 0,
      text: "",
      style: { ...{ color: "#2962ff", width: 1, lineStyle: "dashed" as const, extendLeft: false, extendRight: false, fill: null } },
    };
  }

  private xy(anchor: Anchor): { x: number; y: number } | null {
    const chart = this.chart;
    const series = this.series;
    if (!chart || !series) return null;
    const mapped = mapAnchor(anchor, this.scene.timeframe);
    const x = chart.timeScale().timeToCoordinate(mapped.time as UTCTimestamp);
    const y = series.priceToCoordinate(mapped.price);
    if (x == null || y == null) return null;
    return { x, y };
  }

  private shapeFor(drawing: Drawing, width: number, height: number): Shape | null {
    const series = this.series;
    if (!series) return null;
    const a = drawing.anchors[0];
    if (!a) return null;
    const b = drawing.anchors[1];
    const pa = this.xy(a);
    const pb = b ? this.xy(b) : null;
    const style = drawing.style;
    const handles = drawing.anchors.flatMap((anchor, index) => {
      const p = this.xy(anchor);
      return p ? [{ ...p, index }] : [];
    });
    const base = { id: drawing.id, style, handles, fill: null, labels: [] as Shape["labels"] };

    if (drawing.tool === "horizontal" || drawing.tool === "horizontal_ray") {
      const y = series.priceToCoordinate(a.price);
      if (y == null) return null;
      const x = pa?.x ?? 0;
      const x1 = style.extendLeft ? 0 : x;
      const x2 = style.extendRight ? width : x;
      return { ...base, segments: [{ x1, y1: y, x2: x2 === x1 ? x1 + 1 : x2, y2: y }] };
    }
    if (drawing.tool === "vertical") {
      if (!pa) return null;
      return { ...base, segments: [{ x1: pa.x, y1: 0, x2: pa.x, y2: height }] };
    }
    if (drawing.tool === "text") {
      if (!pa) return null;
      const text = drawing.text || "Text";
      const w = Math.max(24, text.length * 7);
      return {
        ...base,
        segments: [{ x1: pa.x, y1: pa.y - 8, x2: pa.x + w, y2: pa.y - 8 }],
        labels: [{ x: pa.x, y: pa.y, text }],
      };
    }
    if (!pa || !pb) return null;
    if (drawing.tool === "rectangle") {
      const x = Math.min(pa.x, pb.x);
      const y = Math.min(pa.y, pb.y);
      const w = Math.abs(pb.x - pa.x);
      const h = Math.abs(pb.y - pa.y);
      return {
        ...base,
        fill: { x, y, w, h },
        segments: [
          { x1: x, y1: y, x2: x + w, y2: y },
          { x1: x + w, y1: y, x2: x + w, y2: y + h },
          { x1: x + w, y1: y + h, x2: x, y2: y + h },
          { x1: x, y1: y + h, x2: x, y2: y },
        ],
      };
    }
    if (drawing.tool === "fib" && b) {
      const levels = fibPrices(a, b);
      const x1 = Math.min(pa.x, pb.x);
      const x2 = Math.max(pa.x, pb.x);
      const segments: Seg[] = [];
      const labels: Shape["labels"] = [];
      for (const level of levels) {
        const y = series.priceToCoordinate(level.price);
        if (y == null) continue;
        segments.push({ x1, y1: y, x2, y2: y });
        labels.push({ x: x2 + 4, y: y + 4, text: `${level.ratio}  ${level.price.toFixed(2)}` });
      }
      return { ...base, segments, labels };
    }
    if (drawing.tool === "measure" && b) {
      const m = measure(a, b, this.scene.timeframe);
      return {
        ...base,
        segments: [{ x1: pa.x, y1: pa.y, x2: pb.x, y2: pb.y }],
        labels: [{ x: pb.x + 6, y: pb.y, text: `${m.price.toFixed(2)} (${m.percent.toFixed(2)}%)  ${m.bars} bars` }],
      };
    }
    return { ...base, segments: [extendLine(pa, pb, width, style.extendLeft, style.extendRight)] };
  }
}

function extendLine(
  a: { x: number; y: number },
  b: { x: number; y: number },
  width: number,
  extendLeft: boolean,
  extendRight: boolean,
): Seg {
  if (a.x === b.x) return { x1: a.x, y1: a.y, x2: b.x, y2: b.y };
  const slope = (b.y - a.y) / (b.x - a.x);
  const at = (x: number) => a.y + slope * (x - a.x);
  let x1 = a.x;
  let x2 = b.x;
  if (extendRight) x2 = b.x >= a.x ? width : 0;
  if (extendLeft) x1 = b.x >= a.x ? 0 : width;
  return { x1, y1: at(x1), x2, y2: at(x2) };
}

export interface FvgStyle {
  bull: string;
  bear: string;
}

export interface FvgLayer {
  boxes: readonly FvgBox[];
  colors: FvgStyle;
}

export class FvgPrimitive implements ISeriesPrimitive {
  layers: readonly FvgLayer[] = [];
  private chart: Chart | null = null;
  private series: Series | null = null;
  private requestUpdate: () => void = () => {};
  private readonly views: readonly IPrimitivePaneView[];

  constructor() {
    const renderer: IPrimitivePaneRenderer = {
      draw: (target: CanvasRenderingTarget2D) => {
        target.useMediaCoordinateSpace(({ context, mediaSize }) => this.paint(context, mediaSize.width));
      },
    };
    this.views = [{ renderer: () => renderer }];
  }

  attached(param: SeriesAttachedParameter<Time, "Candlestick">): void {
    this.chart = param.chart as Chart;
    this.series = param.series as Series;
    this.requestUpdate = param.requestUpdate;
  }

  detached(): void {
    this.chart = null;
    this.series = null;
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return this.views;
  }

  setLayers(layers: readonly FvgLayer[]): void {
    this.layers = layers;
    this.requestUpdate();
  }

  private paint(ctx: CanvasRenderingContext2D, width: number): void {
    const chart = this.chart;
    const series = this.series;
    if (!chart || !series) return;
    for (const layer of this.layers) for (const box of layer.boxes) {
      const x1 = chart.timeScale().timeToCoordinate(box.start_time as UTCTimestamp);
      const yTop = series.priceToCoordinate(box.top);
      const yBot = series.priceToCoordinate(box.bottom);
      if (x1 == null || yTop == null || yBot == null) continue;
      const xEnd = box.end_time == null ? null : chart.timeScale().timeToCoordinate(box.end_time as UTCTimestamp);
      const x2 = box.extends ? width : (xEnd ?? x1);
      const x = Math.min(x1, x2);
      const w = Math.max(1, Math.abs(x2 - x1));
      const y = Math.min(yTop, yBot);
      const h = Math.max(1, Math.abs(yBot - yTop));
      ctx.save();
      ctx.globalAlpha = box.faded ? 0.12 : 0.22;
      ctx.fillStyle = box.direction === "bull" ? layer.colors.bull : layer.colors.bear;
      ctx.fillRect(x, y, w, h);
      ctx.restore();
    }
  }
}
