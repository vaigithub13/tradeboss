import type { CanvasRenderingTarget2D } from "fancy-canvas";
import type {
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesPrimitive,
  SeriesAttachedParameter,
  Time,
  UTCTimestamp,
} from "lightweight-charts";

/** Window in one tint, the following candles in another. Context only. */
export interface PatternShade {
  start: number;
  end: number;
  forwardEnd: number;
}

export class SimilarShadePrimitive implements ISeriesPrimitive<Time> {
  private span: PatternShade | null = null;
  private chart: IChartApi | null = null;
  private requestUpdate: () => void = () => {};
  private readonly views: readonly IPrimitivePaneView[];

  constructor() {
    const renderer: IPrimitivePaneRenderer = {
      draw: (target: CanvasRenderingTarget2D) => {
        target.useMediaCoordinateSpace(({ context, mediaSize }) => this.paint(context, mediaSize.height));
      },
    };
    this.views = [{ renderer: () => renderer, zOrder: () => "bottom" }];
  }

  attached(param: SeriesAttachedParameter<Time>): void {
    this.chart = param.chart;
    this.requestUpdate = param.requestUpdate;
  }

  detached(): void {
    this.chart = null;
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return this.views;
  }

  setSpan(span: PatternShade | null): void {
    this.span = span;
    this.requestUpdate();
  }

  private paint(ctx: CanvasRenderingContext2D, height: number): void {
    const chart = this.chart;
    const span = this.span;
    if (!chart || !span) return;
    const scale = chart.timeScale();
    const x1 = scale.timeToCoordinate(span.start as UTCTimestamp);
    const x2 = scale.timeToCoordinate(span.end as UTCTimestamp);
    const x3 = scale.timeToCoordinate(span.forwardEnd as UTCTimestamp);
    if (x1 == null || x2 == null) return;
    ctx.save();
    ctx.fillStyle = "rgba(96, 165, 250, 0.18)";
    ctx.fillRect(Math.min(x1, x2), 0, Math.abs(x2 - x1) + 4, height);
    if (x3 != null) {
      ctx.fillStyle = "rgba(251, 191, 36, 0.16)";
      ctx.fillRect(Math.min(x2, x3), 0, Math.abs(x3 - x2) + 4, height);
    }
    ctx.restore();
  }
}
