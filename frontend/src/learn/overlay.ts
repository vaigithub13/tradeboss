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
}

/** Short horizontal labels (BOS / CHoCH) from the swing to the bar that closed beyond it. */
export class LearnOverlay implements ISeriesPrimitive<Time> {
  segments: LearnSegment[] = [];
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

  setSegments(segments: LearnSegment[]): void {
    this.segments = segments;
    this.requestUpdate();
  }

  private paint(ctx: CanvasRenderingContext2D): void {
    const chart = this.chart;
    const series = this.series;
    if (!chart || !series) return;
    ctx.save();
    ctx.font = "11px sans-serif";
    ctx.lineWidth = 1.5;
    for (const seg of this.segments) {
      const x1 = chart.timeScale().timeToCoordinate(seg.from as UTCTimestamp);
      const x2 = chart.timeScale().timeToCoordinate(seg.to as UTCTimestamp);
      const y = series.priceToCoordinate(seg.price);
      if (x1 == null || x2 == null || y == null) continue;
      ctx.strokeStyle = seg.color;
      ctx.beginPath();
      ctx.moveTo(x1, y);
      ctx.lineTo(x2, y);
      ctx.stroke();
      ctx.fillStyle = seg.color;
      ctx.fillText(seg.text, Math.max(x1, x2) + 4, y - 3);
    }
    ctx.restore();
  }
}
