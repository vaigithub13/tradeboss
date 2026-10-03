import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  TickMarkType,
  LineStyle,
  createChart,
  createSeriesMarkers,
  type CandlestickData,
  type HistogramData,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
  type TickMarkFormatter,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import type { Candle, Timeframe } from "../api/client";
import { indicatorKey } from "../indicators/cache";
import { legendRows } from "../indicators/legend";
import { tailChange } from "../live/merge";
import { useIndicatorStore } from "../store/indicatorStore";
import { formatCrosshairTime, formatTick, legendValues, type TickKind } from "./format";
import { IndicatorLayer } from "./indicatorLayer";
import { jumpWindow } from "../backtest/present";
import {
  indexOfTime,
  initialRange,
  loadLegendCollapsed,
  needsNewer,
  needsOlder,
  saveLegendCollapsed,
  shiftRange,
  snapshotOf,
  type Snapshot,
  windowShift,
} from "./view";
import { hasVolume } from "./volume";
import { browserStorage } from "../indicators/persistence";
import { clipSeries, seriesThroughCursor } from "../replay/cap";

const UP = "#26a69a";
const DOWN = "#ef5350";
const BG = "#0b0e14";
const GRID = "#1a1f2b";
const BORDER = "#2a2e39";
const INITIAL_VISIBLE_BARS = 150;

const TICK_KIND: Record<TickMarkType, TickKind> = {
  [TickMarkType.Year]: "year",
  [TickMarkType.Month]: "month",
  [TickMarkType.DayOfMonth]: "day",
  [TickMarkType.Time]: "time",
  [TickMarkType.TimeWithSeconds]: "time",
};

const tickMarkFormatter: TickMarkFormatter = (time, type) =>
  formatTick(Number(time), TICK_KIND[type]);

interface Props {
  /** shown in the legend (e.g. NIFTY, RELIANCE) */
  displayName: string;
  timeframe: Timeframe;
  /** identifies symbol + timeframe + sessions: a change means "new data set", not "older chunk" */
  scope: string;
  candles: readonly Candle[];
  loadingOlder: boolean;
  loadingNewer: boolean;
  /** called when the user scrolls close to the left edge of the loaded history */
  onNeedOlder: () => void;
  /** called when the user scrolls close to the right edge and newer bars were dropped from the window */
  onNeedNewer: () => void;
  markers?: SeriesMarker<Time>[];
  /** bump `token` to scroll the entry bar about a third of the way across */
  focus?: { token: number; time: number } | null;
  onPickTime?: (time: number) => void;
  tradeCard?: ReactNode;
  levels?: { price: number; title: string; color: string }[];
  /** Replay cursor. The series last-price line is the last bar at or before it. */
  cursor?: number | null;
}

export function ChartView({
  displayName, timeframe, scope, candles: loadedCandles, loadingOlder, loadingNewer, onNeedOlder, onNeedNewer,
  markers = [], focus = null, onPickTime, tradeCard, levels = [], cursor = null,
}: Props) {
  const candles = useMemo(() => seriesThroughCursor(loadedCandles, cursor).bars, [loadedCandles, cursor]);
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const timeframeRef = useRef(timeframe);
  const candlesRef = useRef<readonly Candle[]>(candles);
  const snapshotRef = useRef<Snapshot | null>(null);
  const onNeedOlderRef = useRef(onNeedOlder);
  onNeedOlderRef.current = onNeedOlder;
  const onNeedNewerRef = useRef(onNeedNewer);
  onNeedNewerRef.current = onNeedNewer;
  const layerRef = useRef<IndicatorLayer | null>(null);
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const priceLinesRef = useRef<IPriceLine[]>([]);
  const onPickTimeRef = useRef(onPickTime);
  onPickTimeRef.current = onPickTime;
  /** start time of the candle under the crosshair (null = latest candle) */
  const [hoverTime, setHoverTime] = useState<number | null>(null);
  const [collapsed, setCollapsed] = useState(() => loadLegendCollapsed(browserStorage()));

  const items = useIndicatorStore((s) => s.items);
  const cached = useIndicatorStore((s) => s.data[scope]);

  const volumeVisible = useMemo(() => hasVolume(candles), [candles]);

  const toggleCollapsed = (): void => {
    setCollapsed((c) => {
      saveLegendCollapsed(!c, browserStorage());
      return !c;
    });
  };

  // ---- create the chart once
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const chart = createChart(el, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: BG },
        textColor: "#d1d4dc",
        fontSize: 12,
        panes: { separatorColor: BORDER, separatorHoverColor: "#3b4252" },
      },
      grid: { vertLines: { color: GRID }, horzLines: { color: GRID } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: BORDER },
      timeScale: {
        borderColor: BORDER,
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 5,
        tickMarkFormatter,
      },
      localization: {
        timeFormatter: (time: Time) => formatCrosshairTime(Number(time), timeframeRef.current),
      },
      handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true },
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: UP,
      downColor: DOWN,
      wickUpColor: UP,
      wickDownColor: DOWN,
      borderVisible: false,
      priceLineVisible: true,
      priceFormat: { type: "price", precision: 2, minMove: 0.05 },
    });

    chart.subscribeClick((param) => {
      if (param.time !== undefined) onPickTimeRef.current?.(Number(param.time));
    });

    chart.subscribeCrosshairMove((param) => {
      const t = param.time;
      const idx = t === undefined || !param.point ? undefined : indexOfTime(candlesRef.current, Number(t));
      setHoverTime(idx === undefined ? null : Number(t));
    });

    // Lazy loading: ask for an older chunk when the view gets close to the left edge.
    chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (!range) return;
      if (needsOlder(range)) onNeedOlderRef.current();
      if (needsNewer(range, candlesRef.current.length)) onNeedNewerRef.current();
    });

    if (import.meta.env.DEV) (window as unknown as { __chart?: unknown }).__chart = { chart, candleSeries };

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    layerRef.current = new IndicatorLayer(chart);
    return () => {
      layerRef.current?.dispose();
      markersRef.current = null;
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      volumeSeriesRef.current = null;
      layerRef.current = null;
    };
  }, []);

  useEffect(() => {
    const series = candleSeriesRef.current;
    if (!series) return;
    if (!markersRef.current) markersRef.current = createSeriesMarkers(series);
    markersRef.current.setMarkers(markers);
  }, [markers, candles]);

  useEffect(() => {
    const series = candleSeriesRef.current;
    if (!series) return;
    for (const line of priceLinesRef.current) series.removePriceLine(line);
    priceLinesRef.current = levels.map((level) =>
      series.createPriceLine({
        price: level.price,
        color: level.color,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: level.title,
      }),
    );
  }, [levels, candles]);

  // ---- push data whenever candles change
  useEffect(() => {
    const chart = chartRef.current;
    const candleSeries = candleSeriesRef.current;
    if (!chart || !candleSeries) return;

    timeframeRef.current = timeframe;
    const prevCandles = candlesRef.current;
    candlesRef.current = candles;

    // Live tick (the newest candle changed and/or one was appended, same data set): update the
    // tail only. A full setData per tick would re-send the whole history and reset the view.
    const tail = snapshotRef.current?.scope === scope ? tailChange(prevCandles, candles) : null;
    if (tail !== null && volumeVisible === (volumeSeriesRef.current !== null)) {
      for (let i = tail; i < candles.length; i++) {
        const c = candles[i];
        if (!c) continue;
        candleSeries.update({ time: c.time as UTCTimestamp, open: c.open, high: c.high, low: c.low, close: c.close });
        volumeSeriesRef.current?.update({
          time: c.time as UTCTimestamp,
          value: c.volume,
          color: c.close >= c.open ? `${UP}80` : `${DOWN}80`,
        });
      }
      snapshotRef.current = snapshotOf(scope, candles);
      return;
    }

    // The loaded window slid (older chunk prepended, or a newer one appended with far bars dropped):
    // keep the same bars on screen, no jump.
    const shift = windowShift(snapshotRef.current, scope, candles);
    const savedRange = shift !== null ? chart.timeScale().getVisibleLogicalRange() : null;
    snapshotRef.current = snapshotOf(scope, candles);

    candleSeries.setData(
      candles.map<CandlestickData<UTCTimestamp>>((c) => ({
        time: c.time as UTCTimestamp,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
      })),
    );

    // Volume pane: only when the loaded range has real volume (index data is all 0).
    // Indicator panes sit below it, so they are re-created whenever the volume pane appears/disappears.
    if (volumeVisible !== (volumeSeriesRef.current !== null)) layerRef.current?.clear();
    if (volumeVisible) {
      let vol = volumeSeriesRef.current;
      if (!vol) {
        vol = chart.addSeries(
          HistogramSeries,
          { priceFormat: { type: "volume" }, priceLineVisible: false, lastValueVisible: false },
          1,
        );
        volumeSeriesRef.current = vol;
        chart.panes()[1]?.setStretchFactor(0.25);
      }
      vol.setData(
        candles.map<HistogramData<UTCTimestamp>>((c) => ({
          time: c.time as UTCTimestamp,
          value: c.volume,
          color: c.close >= c.open ? `${UP}80` : `${DOWN}80`,
        })),
      );
    } else if (volumeSeriesRef.current) {
      chart.removeSeries(volumeSeriesRef.current);
      volumeSeriesRef.current = null;
      if (chart.panes().length > 1) chart.removePane(1);
    }

    // Everything above ran synchronously in this tick, so the chart never paints an intermediate state.
    if (savedRange && shift !== null) {
      chart.timeScale().setVisibleLogicalRange(shiftRange(savedRange, shift));
    } else if (candles.length > 0) {
      chart.timeScale().setVisibleLogicalRange(initialRange(candles.length, INITIAL_VISIBLE_BARS));
      performance.mark("chart:data-set");
    }
  }, [candles, timeframe, scope, volumeVisible]);

  const appliedFocus = useRef(0);
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !focus || appliedFocus.current === focus.token) return;
    const placed = jumpWindow(candles.map((c) => c.time), focus.time);
    if (placed.kind !== "range") {
      if (candles.length > 0) appliedFocus.current = focus.token;
      return;
    }
    appliedFocus.current = focus.token;
    chart.timeScale().setVisibleLogicalRange({ from: placed.from, to: placed.to });
  }, [focus, candles]);

  // ---- indicators: draw what is cached for this data set (nothing stale can be here: the cache is keyed by scope)
  useEffect(() => {
    layerRef.current?.sync(items, (item) => clipSeries(cached?.[indicatorKey(item.type, item.params)], cursor));
  }, [items, cached, volumeVisible, candles, cursor]);

  const lastIndex = candles.length - 1;
  const shownIndex = (hoverTime === null ? undefined : indexOfTime(candles, hoverTime)) ?? lastIndex;
  const shown = candles[shownIndex];
  const legend = useMemo(
    () => (shown ? legendValues(shown, shownIndex > 0 ? (candles[shownIndex - 1]?.close ?? null) : null, volumeVisible) : null),
    [shown, shownIndex, candles, volumeVisible],
  );
  const indicatorRows = useMemo(
    () =>
      legendRows({
        items,
        entryFor: (item) => clipSeries(cached?.[indicatorKey(item.type, item.params)], cursor),
        candles,
        timeframe,
        time: shown?.time ?? null,
      }),
    [items, cached, candles, timeframe, shown, cursor],
  );

  return (
    <div className="relative h-full w-full">
      <div ref={containerRef} className="absolute inset-0" data-testid="chart" />
      <div
        className="absolute left-2 top-2 z-10 max-w-[calc(100%-5rem)] select-none rounded bg-black/45 px-2 py-1 font-mono text-[11px] leading-[18px] backdrop-blur-[2px]"
        data-testid="legend-box"
      >
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={toggleCollapsed}
            aria-label={collapsed ? "Expand legend" : "Collapse legend"}
            aria-expanded={!collapsed}
            title={collapsed ? "Expand legend" : "Collapse legend"}
            className="pointer-events-auto -ml-0.5 w-4 shrink-0 cursor-pointer text-white/50 hover:text-white"
            data-testid="legend-toggle"
          >
            {collapsed ? "▸" : "▾"}
          </button>
          <span className="font-semibold text-white/80">
            {displayName} · {timeframe}
          </span>
          {shown && (
            <span className="text-white/60" data-testid="legend-time">
              {formatCrosshairTime(shown.time, timeframe)}
            </span>
          )}
          {loadingOlder && <span className="text-white/35">loading older…</span>}
          {loadingNewer && <span className="text-white/35">loading newer…</span>}
        </div>
        {!collapsed && legend && (
          <div className="pointer-events-none flex flex-wrap items-center gap-x-2.5" data-testid="legend">
            {(["open", "high", "low", "close"] as const).map((k) => (
              <span key={k} className="text-white/45">
                {k[0]?.toUpperCase()} <span className={legend.up ? "text-emerald-400" : "text-red-400"}>{legend[k]}</span>
              </span>
            ))}
            <span className={legend.up ? "text-emerald-400" : "text-red-400"}>
              {legend.change} ({legend.changePct})
            </span>
            {legend.volume !== null && <span className="text-white/45">Vol {legend.volume}</span>}
          </div>
        )}
        {!collapsed && indicatorRows.length > 0 && (
          <div className="pointer-events-none flex flex-col" data-testid="indicator-legend">
            {indicatorRows.map((row) => (
              <div key={row.id} className="flex flex-wrap items-center gap-x-2" data-testid={`legend-${row.id}`}>
                <span className="text-white/45">{row.name}</span>
                {row.unavailable ? (
                  <span className="text-amber-400/80">unavailable</span>
                ) : (
                  row.entries.map((e, i) => (
                    <span key={i} style={{ color: e.color }}>
                      {e.label && <span className="mr-1 text-white/35">{e.label}</span>}
                      {e.text}
                    </span>
                  ))
                )}
              </div>
            ))}
          </div>
        )}
      </div>
      {tradeCard}
    </div>
  );
}
