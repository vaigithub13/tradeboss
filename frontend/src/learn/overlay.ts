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

interface Anchor {
  x: number;
  y: number;
  text: string;
  color: string;
  above: boolean;
  w: number;
}

/** Boxes, short BOS / CHoCH tags, and stacked swing labels. Font is 9px. */
export class LearnOverlay implements ISeriesPrimitive<Time> {
  segments: LearnSegment[] = [];
  marks: LearnMark[] = [];
  boxes: LearnBox[] = [];
  private chart: IChartApi | null = null;
  private series: ISeriesApi<"Candlestick"> | null = null;
  private requestUpdate: () => void = () => {};
  private readonly views: readonly IPrimitivePaneView[];

  constructor() {
    const renderer: IPrimitivePaneRenderer = {
      draw: (target: CanvasRenderingTarget2D) => {
        target.useMediaCoordinateSpace(({ context }) => this.paint(context));
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

  private paint(ctx: CanvasRenderingContext2D): void {
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
