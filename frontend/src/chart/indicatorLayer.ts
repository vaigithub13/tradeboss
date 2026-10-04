import type { CanvasRenderingTarget2D } from "fancy-canvas";
import {
  HistogramSeries,
  LineSeries,
  LineStyle,
  LineType,
  type IChartApi,
  type IPriceLine,
  type IPrimitivePaneRenderer,
  type IPrimitivePaneView,
  type ISeriesApi,
  type ISeriesPrimitive,
  type LineWidth,
  type SeriesAttachedParameter,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";

import type { IndicatorEntry } from "../indicators/cache";
import type { IndicatorInstance, IndicatorType } from "../indicators/catalog";
import {
  firstBarOfIstDay,
  histogramPoints,
  linePoints,
  supertrendPoints,
  type HistogramPoint,
  type LinePoint,
} from "../indicators/seriesData";

type AnySeries = ISeriesApi<"Line"> | ISeriesApi<"Histogram">;

interface StochGuide {
  role: "upper" | "middle" | "lower";
  line: IPriceLine;
}

interface Bundle {
  type: IndicatorType;
  /** series keyed by role, e.g. { basis, upper, lower } or { up, down } */
  series: Record<string, AnySeries>;
  /** what the series currently hold: the entry object and the colours baked into the data */
  filledWith: { entry: IndicatorEntry | undefined; dataColors: string } | null;
  /** bumped on every refill; queued slices of an older fill are dropped */
  version: number;
  guides: StochGuide[];
  bandFill: StochBandFill | null;
}

/**
 * Handing N points to Lightweight Charts costs O(N) per series (about 1 us per point), so with a
 * long history one indicator would block the page for a long time. Above this many points the
 * work is cut into one series per macrotask, letting the browser paint and handle input in between.
 */
const SLICE_THRESHOLD = 20000;

const SEPARATE_PANE_STRETCH = 0.35;
const GUIDE_COLOR = "#8b93a7";
const NONE = [] as const;

const asLine = (points: readonly LinePoint[]) =>
  points.map((p) => {
    const base = { time: p.time as UTCTimestamp };
    if (p.value === undefined) return base;
    return p.color ? { ...base, value: p.value, color: p.color } : { ...base, value: p.value };
  });

const asHist = (points: readonly HistogramPoint[]) =>
  points.map((p) =>
    p.value === undefined
      ? { time: p.time as UTCTimestamp }
      : { time: p.time as UTCTimestamp, value: p.value, ...(p.color ? { color: p.color } : {}) },
  );

/**
 * Owns the Lightweight Charts series of every indicator. Price-pane indicators draw over the
 * candles; RSI, Stochastic, and MACD each get their own pane below (after the volume pane, if any).
 * All lines are straight segments (LineType.Simple), never curved.
 */
export class IndicatorLayer {
  private readonly bundles = new Map<string, Bundle>();
  private readonly queue: (() => void)[] = [];
  private timer: ReturnType<typeof setTimeout> | null = null;

  constructor(private readonly chart: IChartApi) {}

  /** Remove every indicator series (and the panes that become empty). */
  clear(): void {
    for (const id of [...this.bundles.keys()]) this.drop(id);
    this.queue.length = 0;
  }

  /** Stop pending sliced updates (chart is going away). */
  dispose(): void {
    this.queue.length = 0;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
  }

  private pump = (): void => {
    this.timer = null;
    this.queue.shift()?.();
    if (this.queue.length > 0) this.timer = setTimeout(this.pump, 0);
  };

  private enqueue(job: () => void): void {
    this.queue.push(job);
    if (this.timer === null) this.timer = setTimeout(this.pump, 0);
  }

  /**
   * Make the chart show exactly the visible `items`, filled from `entryFor` (undefined = nothing
   * cached yet). Series whose values did not change are left alone: recolouring only touches
   * options, it never re-sends data.
   */
  sync(items: readonly IndicatorInstance[], entryFor: (item: IndicatorInstance) => IndicatorEntry | undefined): void {
    const wanted = new Map(items.filter((i) => i.visible).map((i) => [i.id, i]));
    for (const [id, bundle] of [...this.bundles]) {
      const item = wanted.get(id);
      if (!item || item.type !== bundle.type) this.drop(id);
    }
    for (const item of wanted.values()) {
      if (!this.bundles.has(item.id)) this.bundles.set(item.id, this.create(item));
    }
    for (const item of wanted.values()) {
      const bundle = this.bundles.get(item.id);
      if (bundle) this.fill(item, bundle, entryFor(item));
    }
  }

  private drop(id: string): void {
    const bundle = this.bundles.get(id);
    if (!bundle) return;
    this.bundles.delete(id);
    for (const s of Object.values(bundle.series)) {
      try {
        this.chart.removeSeries(s);
      } catch {
        // already gone (chart disposed)
      }
    }
    // Defensive: LWC removes emptied panes itself, but never leave an empty one behind.
    const panes = this.chart.panes();
    for (let i = panes.length - 1; i >= 1; i--) {
      if (panes[i]?.getSeries().length === 0) this.chart.removePane(i);
    }
  }

  /** Index a new pane would get (addSeries creates it on demand). */
  private newPane(): number {
    return this.chart.panes().length;
  }

  private create(item: IndicatorInstance): Bundle {
    const c = item.colors;
    const color = (k: string): string => c[k] ?? "#d1d4dc";
    const line = (col: string, pane: number, width: LineWidth = 2, extra: object = {}) =>
      this.chart.addSeries(
        LineSeries,
        {
          color: col,
          lineWidth: width,
          lineType: LineType.Simple,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
          ...extra,
        },
        pane,
      );
    const bundle = (type: IndicatorType, series: Record<string, AnySeries>): Bundle => ({
      type,
      series,
      filledWith: null,
      version: 0,
      guides: [],
      bandFill: null,
    });

    switch (item.type) {
      case "sma":
      case "ema":
      case "vwap":
      case "vwap_fut":
        return bundle(item.type, { line: line(color("line"), 0) });
      case "bb":
        return bundle("bb", {
          upper: line(color("band"), 0, 1),
          lower: line(color("band"), 0, 1),
          basis: line(color("basis"), 0, 1),
        });
      case "supertrend":
        return bundle("supertrend", { up: line(color("up"), 0), down: line(color("down"), 0) });
      case "rsi": {
        const pane = this.newPane();
        const rsi = line(color("line"), pane, 2, {
          lastValueVisible: true,
          priceFormat: { type: "price", precision: 2, minMove: 0.01 },
          autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 100 } }),
        });
        for (const price of [70, 30]) {
          rsi.createPriceLine({
            price,
            color: GUIDE_COLOR,
            lineWidth: 1,
            lineStyle: LineStyle.Dashed,
            axisLabelVisible: false,
            title: "",
          });
        }
        this.chart.panes()[pane]?.setStretchFactor(SEPARATE_PANE_STRETCH);
        return bundle("rsi", { line: rsi });
      }
      case "stoch": {
        const pane = this.newPane();
        const scale = {
          lastValueVisible: true,
          priceFormat: { type: "price", precision: 2, minMove: 0.01 },
          autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 100 } }),
        };
        const k = line(color("k"), pane, lineWidth(item.params["k_width"]), scale);
        const d = line(color("d"), pane, lineWidth(item.params["d_width"]), { ...scale, lastValueVisible: true });
        const bandFill = new StochBandFill();
        k.attachPrimitive(bandFill);
        const guides = (["upper", "middle", "lower"] as const).map((role) => ({
          role,
          line: k.createPriceLine({
            price: Number(item.params[role] ?? 0),
            color: color("band"),
            lineWidth: lineWidth(item.params["band_width"]),
            lineStyle: LineStyle.Solid,
            lineVisible: item.params["show_bands"] !== "hide",
            axisLabelVisible: false,
            title: "",
          }),
        }));
        this.chart.panes()[pane]?.setStretchFactor(SEPARATE_PANE_STRETCH);
        const created = bundle("stoch", { k, d });
        created.guides = guides;
        created.bandFill = bandFill;
        return created;
      }
      case "macd": {
        const pane = this.newPane();
        const hist = this.chart.addSeries(
          HistogramSeries,
          { priceLineVisible: false, lastValueVisible: false, priceFormat: { type: "price", precision: 2, minMove: 0.01 } },
          pane,
        );
        hist.createPriceLine({
          price: 0,
          color: GUIDE_COLOR,
          lineWidth: 1,
          lineStyle: LineStyle.Solid,
          axisLabelVisible: false,
          title: "",
        });
        const macd = line(color("macd"), pane, 2);
        const signal = line(color("signal"), pane, 2);
        this.chart.panes()[pane]?.setStretchFactor(SEPARATE_PANE_STRETCH);
        return bundle("macd", { hist, macd, signal });
      }
      case "fvg":
        return bundle("fvg", {});
    }
  }

  /** The (role -> points) data jobs for one indicator and the values in `entry`. */
  private jobsFor(
    item: IndicatorInstance,
    entry: IndicatorEntry | undefined,
    color: (k: string) => string,
  ): { role: string; points: () => readonly LinePoint[] | readonly HistogramPoint[] }[] {
    const t = entry?.times ?? NONE;
    const out = (key: string): readonly (number | null)[] => entry?.outputs[key] ?? NONE;
    const jobs: { role: string; points: () => readonly LinePoint[] | readonly HistogramPoint[] }[] = [];
    const lineJob = (role: string, points: () => readonly LinePoint[]): void => void jobs.push({ role, points });

    switch (item.type) {
      case "sma":
      case "ema":
        lineJob("line", () => linePoints(t, out(item.type)));
        break;
      case "vwap":
      case "vwap_fut":
        // VWAP restarts every IST day: do not join yesterday's last value to today's first
        lineJob("line", () => linePoints(t, out("vwap"), firstBarOfIstDay(t)));
        break;
      case "bb":
        lineJob("basis", () => linePoints(t, out("basis")));
        lineJob("upper", () => linePoints(t, out("upper")));
        lineJob("lower", () => linePoints(t, out("lower")));
        break;
      case "supertrend": {
        // up and down come from one pass; compute it lazily and once
        let both: ReturnType<typeof supertrendPoints> | null = null;
        const pair = () => (both ??= supertrendPoints(t, out("supertrend"), out("direction")));
        lineJob("up", () => pair().up);
        lineJob("down", () => pair().down);
        break;
      }
      case "rsi":
        lineJob("line", () => linePoints(t, out("rsi")));
        break;
      case "stoch":
        lineJob("k", () => linePoints(t, out("k")));
        lineJob("d", () => linePoints(t, out("d")));
        break;
      case "macd":
        lineJob("macd", () => linePoints(t, out("macd")));
        lineJob("signal", () => linePoints(t, out("signal")));
        jobs.push({ role: "hist", points: () => histogramPoints(t, out("hist"), color("histUp"), color("histDown")) });
        break;
      case "fvg":
        break;
    }

    return jobs;
  }

  /**
   * Live update: the entry only grew by (at most) one bar on the right and its newest values
   * changed. Push the last few points with series.update (O(1) per series) instead of re-sending
   * the whole history. The leading point is context for the point builders (line breaks, day
   * starts) and is never drawn.
   */
  private pushTail(
    item: IndicatorInstance,
    bundle: Bundle,
    entry: IndicatorEntry,
    prevLen: number,
    color: (k: string) => string,
  ): void {
    // lightweight-charts only accepts updates at the series' last time or later: start at the
    // previously newest bar, with one earlier bar as (undrawn) context for the point builders.
    const from = Math.max(0, prevLen - 2);
    const tail: IndicatorEntry = {
      times: entry.times.slice(from),
      outputs: Object.fromEntries(Object.entries(entry.outputs).map(([k, v]) => [k, v.slice(from)])),
    };
    const firstDrawn = entry.times[prevLen - 1] ?? 0;
    const s = bundle.series as Record<string, ISeriesApi<"Line">>;
    for (const job of this.jobsFor(item, tail, color)) {
      const pts = job.points().filter((p) => p.time >= firstDrawn);
      try {
        if (job.role === "hist") {
          for (const p of asHist(pts as HistogramPoint[])) (bundle.series["hist"] as ISeriesApi<"Histogram"> | undefined)?.update(p);
        } else {
          for (const p of asLine(pts as LinePoint[])) s[job.role]?.update(p);
        }
      } catch {
        // series removed in the meantime
      }
    }
  }

  /** Colours, widths, band levels, and the background fill. Cheap, so it runs even when the values are unchanged. */
  private syncStoch(item: IndicatorInstance, bundle: Bundle): void {
    if (item.type !== "stoch" || !bundle.bandFill) return;
    const s = bundle.series as Record<string, ISeriesApi<"Line">>;
    const color = (key: string): string => item.colors[key] ?? "#d1d4dc";
    s["k"]?.applyOptions({ color: color("k"), lineWidth: lineWidth(item.params["k_width"]) });
    s["d"]?.applyOptions({ color: color("d"), lineWidth: lineWidth(item.params["d_width"]) });
    const showBands = item.params["show_bands"] !== "hide";
    const bandColor = color("band");
    const width = lineWidth(item.params["band_width"]);
    for (const guide of bundle.guides) {
      guide.line.applyOptions({
        price: Number(item.params[guide.role] ?? 0),
        color: bandColor,
        lineWidth: width,
        lineVisible: showBands,
      });
    }
    bundle.bandFill.set({
      upper: Number(item.params["upper"] ?? 80),
      lower: Number(item.params["lower"] ?? 20),
      color: hexAlpha(bandColor, 0.1),
      visible: item.params["show_background"] !== "hide",
    });
  }

  private fill(item: IndicatorInstance, bundle: Bundle, entry: IndicatorEntry | undefined): void {
    const c = item.colors;
    const color = (k: string): string => c[k] ?? "#d1d4dc";
    const s = bundle.series as Record<string, ISeriesApi<"Line">>;

    // Colours of lines are series options (cheap). Histogram colours live in the data.
    this.syncStoch(item, bundle);
    const recolour = (role: string, col: string): void => void s[role]?.applyOptions({ color: col });
    switch (item.type) {
      case "sma":
      case "ema":
      case "vwap":
      case "vwap_fut":
      case "rsi":
        recolour("line", color("line"));
        break;
      case "bb":
        recolour("basis", color("basis"));
        recolour("upper", color("band"));
        recolour("lower", color("band"));
        break;
      case "supertrend":
        recolour("up", color("up"));
        recolour("down", color("down"));
        break;
      case "macd":
        recolour("macd", color("macd"));
        recolour("signal", color("signal"));
        break;
      case "stoch":
        break;
    }

    const dataColors = item.type === "macd" ? `${color("histUp")}|${color("histDown")}` : "";
    const prev = bundle.filledWith;
    if (prev && prev.entry === entry && prev.dataColors === dataColors) return; // data unchanged
    bundle.filledWith = { entry, dataColors };

    // A live tick: same left edge, at most one bar more, nothing queued -> update the tail only.
    const before = prev?.entry;
    if (
      before && entry && prev.dataColors === dataColors && this.queue.length === 0 &&
      before.times.length > 3 && before.times[0] === entry.times[0] &&
      entry.times.length - before.times.length >= 0 && entry.times.length - before.times.length <= 1
    ) {
      this.pushTail(item, bundle, entry, before.times.length, color);
      return;
    }

    const jobs = this.jobsFor(item, entry, color);

    const version = ++bundle.version;
    const run = (job: (typeof jobs)[number]): void => {
      if (bundle.version !== version) return; // superseded by a newer fill
      try {
        if (job.role === "hist") {
          (bundle.series["hist"] as ISeriesApi<"Histogram"> | undefined)?.setData(asHist(job.points() as HistogramPoint[]));
        } else {
          s[job.role]?.setData(asLine(job.points() as LinePoint[]));
        }
      } catch {
        // series was removed in the meantime (indicator deleted / chart disposed)
      }
    };
    const sliced = (entry?.times.length ?? 0) > SLICE_THRESHOLD;
    for (const job of jobs) {
      if (sliced) this.enqueue(() => run(job));
      else run(job);
    }
  }
}

function lineWidth(value: unknown): LineWidth {
  const n = Number(value);
  if (n === 2 || n === 3 || n === 4) return n;
  return 1;
}

function hexAlpha(hex: string, alpha: number): string {
  const n = /^#([0-9a-fA-F]{6})$/.exec(hex);
  if (!n?.[1]) return hex;
  const raw = n[1];
  const r = Number.parseInt(raw.slice(0, 2), 16);
  const g = Number.parseInt(raw.slice(2, 4), 16);
  const b = Number.parseInt(raw.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

/** Fills the pane between the upper and lower Stochastic bands. */
class StochBandFill implements ISeriesPrimitive<Time> {
  upper = 80;
  lower = 20;
  color = "rgba(120, 123, 134, 0.1)";
  visible = true;
  private series: ISeriesApi<"Line"> | null = null;
  private requestUpdate: () => void = () => {};
  private readonly renderer: IPrimitivePaneRenderer = {
    draw: (target: CanvasRenderingTarget2D) => {
      target.useMediaCoordinateSpace(({ context, mediaSize }) => this.paint(context, mediaSize.width));
    },
  };
  private readonly views: readonly IPrimitivePaneView[] = [{ zOrder: () => "bottom", renderer: () => this.renderer }];

  attached(param: SeriesAttachedParameter<Time, "Line">): void {
    this.series = param.series;
    this.requestUpdate = param.requestUpdate;
  }

  detached(): void {
    this.series = null;
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return this.views;
  }

  updateAllViews(): void {}

  set(next: { upper: number; lower: number; color: string; visible: boolean }): void {
    this.upper = next.upper;
    this.lower = next.lower;
    this.color = next.color;
    this.visible = next.visible;
    this.requestUpdate();
  }

  private paint(ctx: CanvasRenderingContext2D, width: number): void {
    const series = this.series;
    if (!this.visible || !series) return;
    const y1 = series.priceToCoordinate(this.upper);
    const y2 = series.priceToCoordinate(this.lower);
    if (y1 == null || y2 == null) return;
    ctx.save();
    ctx.fillStyle = this.color;
    ctx.fillRect(0, Math.min(y1, y2), width, Math.abs(y2 - y1));
    ctx.restore();
  }
}
