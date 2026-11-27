import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";

import { fetchCandles, type Candle } from "../api/client";
import { useThemeStore } from "../store/themeStore";
import { candleColors, chartThemeOptions } from "../ui/theme";
import type { ScannerSignal } from "../api/scanner";

const UP = "#26a69a";
const DOWN = "#ef5350";

/** A small 5-minute chart for the selected signal. It is not wired into the Nifty or Sensex live stores. */
export function ScannerChart({ signal }: { signal: ScannerSignal }) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const theme = useThemeStore((s) => s.theme);
  const themeRef = useRef(theme);
  themeRef.current = theme;

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const look = chartThemeOptions(themeRef.current);
    const chart = createChart(el, {
      autoSize: true,
      layout: look.layout,
      grid: look.grid,
      rightPriceScale: look.rightPriceScale,
      timeScale: { ...look.timeScale, timeVisible: true, secondsVisible: false },
    });
    const series = chart.addSeries(CandlestickSeries, {
      ...candleColors(themeRef.current),
      borderVisible: false,
    });
    chartRef.current = chart;
    seriesRef.current = series;
    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, []);

  useEffect(() => {
    chartRef.current?.applyOptions(chartThemeOptions(theme));
    seriesRef.current?.applyOptions(candleColors(theme));
  }, [theme]);

  useEffect(() => {
    const series = seriesRef.current;
    const chart = chartRef.current;
    if (!series || !chart) return;
    const folder = signal.folder || signal.symbol;
    const ac = new AbortController();
    void fetchCandles({ symbol: folder, timeframe: "5m", limit: 120 }, ac.signal)
      .then((page) => {
        if (ac.signal.aborted) return;
        series.setData(
          page.candles.map((c: Candle) => ({
            time: c.time as UTCTimestamp,
            open: c.open,
            high: c.high,
            low: c.low,
            close: c.close,
          })),
        );
        const lines = [
          signal.entry != null ? { price: signal.entry, title: "entry", color: "#64b5f6" } : null,
          signal.stop != null ? { price: signal.stop, title: "SL", color: "#ef5350" } : null,
          signal.target != null ? { price: signal.target, title: "target", color: "#26a69a" } : null,
          signal.range_high != null ? { price: signal.range_high, title: "OR high", color: "#90a4ae" } : null,
          signal.range_low != null ? { price: signal.range_low, title: "OR low", color: "#90a4ae" } : null,
        ].filter((line): line is { price: number; title: string; color: string } => line != null);
        for (const line of lines) {
          series.createPriceLine({ price: line.price, title: line.title, color: line.color, lineWidth: 1, lineStyle: 2, axisLabelVisible: true });
        }
        if (signal.trigger_time) {
          const markers = createSeriesMarkers(series);
          markers.setMarkers([
            {
              time: signal.trigger_time as UTCTimestamp,
              position: signal.side === "short" ? "aboveBar" : "belowBar",
              color: signal.side === "short" ? DOWN : UP,
              shape: signal.side === "short" ? "arrowDown" : "arrowUp",
              text: signal.setup,
            },
          ]);
        }
        chart.timeScale().fitContent();
      })
      .catch(() => {
        // a symbol with no stored candles yet just leaves the pane empty
      });
    return () => ac.abort();
  }, [signal]);

  return <div ref={ref} className="h-64 w-full" data-testid="scanner-chart" />;
}
