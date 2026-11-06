import type { CanvasRenderingTarget2D } from "fancy-canvas";
import type {
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  SeriesAttachedParameter,
  Time,
} from "lightweight-charts";

import { GEX_NEG, GEX_POS, type GexBar } from "./gex";

const MAX_WIDTH_PX = 160;
const MAX_WIDTH_SHARE = 0.18;
const RIGHT_PAD = 4;

/** Net GEX per strike as horizontal bars growing left from the right edge of the price pane. */
export class GexPrimitive implements ISeriesPrimitive<Time> {
  private bars: readonly GexBar[] = [];
  private series: ISeriesApi<"Candlestick"> | null = null;
  private requestUpdate: () => void = () => {};
  private readonly views: readonly IPrimitivePaneView[];

  constructor() {
    const renderer: IPrimitivePaneRenderer = {
      draw: (target: CanvasRenderingTarget2D) => {
        target.useMediaCoordinateSpace(({ context, mediaSize }) => this.paint(context, mediaSize.width));
      },
    };
    this.views = [{ renderer: () => renderer, zOrder: () => "bottom" }];
  }

  attached(param: SeriesAttachedParameter<Time, "Candlestick">): void {
    this.series = param.series as ISeriesApi<"Candlestick">;
    this.requestUpdate = param.requestUpdate;
  }

  detached(): void {
    this.series = null;
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return this.views;
  }

  setBars(bars: readonly GexBar[]): void {
    this.bars = bars;
    this.requestUpdate();
  }

  private paint(ctx: CanvasRenderingContext2D, width: number): void {
    const series = this.series;
    if (!series || this.bars.length === 0) return;
    const ys = this.bars.map((b) => series.priceToCoordinate(b.price));
    let gap = Infinity;
    for (let i = 1; i < ys.length; i++) {
      const a = ys[i - 1];
      const b = ys[i];
      if (a != null && b != null) gap = Math.min(gap, Math.abs(a - b));
    }
    const height = Number.isFinite(gap) ? Math.max(2, Math.min(14, gap * 0.7)) : 6;
    const maxWidth = Math.min(MAX_WIDTH_PX, width * MAX_WIDTH_SHARE);
    ctx.save();
    ctx.globalAlpha = 0.55;
    this.bars.forEach((bar, i) => {
      const y = ys[i];
      if (y == null) return;
      const w = Math.max(1, bar.frac * maxWidth);
      ctx.fillStyle = bar.positive ? GEX_POS : GEX_NEG;
      ctx.fillRect(width - RIGHT_PAD - w, y - height / 2, w, height);
    });
    ctx.restore();
  }
}
