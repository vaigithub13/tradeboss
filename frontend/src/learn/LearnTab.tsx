import { useEffect, useRef, useState } from "react";
import {
  CandlestickSeries,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";

import { capCandlesQuery } from "../replay/cap";
import { fetchCandles, type Candle, type Timeframe } from "../api/client";
import type { LiveView } from "../live/protocol";
import { subscribeLearnBars } from "../store/liveStore";
import { explain } from "./explain";
import { runJob, type JobState, type LearnRead } from "./job";
import { defaultToggles, LAYER_TOGGLES, visibleLabels, type LayerToggles } from "./labels";
import { LearnOverlay } from "./overlay";
import { closedBars, shownBars, type LearnBar } from "./structure";
import { learnChartOptions, learnColors, type LearnTheme } from "./theme";

const SYMBOLS = [
  { id: "NIFTY50", label: "NIFTY" },
  { id: "SENSEX", label: "SENSEX" },
  { id: "BANKNIFTY", label: "BANKNIFTY" },
] as const;

const TIMEFRAMES: Timeframe[] = ["1m", "3m", "5m", "15m", "30m", "1h", "1D"];
const TF_SECONDS: Record<string, number> = {
  "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "1D": 86400,
};

function toBar(candle: Candle): LearnBar {
  return { time: candle.time, open: candle.open, high: candle.high, low: candle.low, close: candle.close };
}

function mergeBars(current: LearnBar[], incoming: readonly LearnBar[]): LearnBar[] {
  const byTime = new Map(current.map((bar) => [bar.time, bar]));
  for (const bar of incoming) byTime.set(bar.time, bar);
  return [...byTime.values()].sort((a, b) => a.time - b.time);
}

export function LearnTab({
  bars,
  onLiveView,
}: {
  bars?: LearnBar[];
  onLiveView?: (view: LiveView | null) => void;
}) {
  const [symbol, setSymbol] = useState<(typeof SYMBOLS)[number]["id"]>("NIFTY50");
  const [timeframe, setTimeframe] = useState<Timeframe>("5m");
  const [theme, setTheme] = useState<LearnTheme>("light");
  const [toggles, setToggles] = useState<LayerToggles>(defaultToggles);
  const [left, setLeft] = useState(3);
  const [right, setRight] = useState(3);
  const [rangeSwings, setRangeSwings] = useState(4);
  const [replay, setReplay] = useState(false);
  const [cursor, setCursor] = useState<number | null>(null);
  const [candles, setCandles] = useState<LearnBar[]>(bars ?? []);
  const [read, setRead] = useState<LearnRead | null>(null);
  const [about, setAbout] = useState(false);
  const [tip, setTip] = useState<string | null>(null);
  const host = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const linesRef = useRef<IPriceLine[]>([]);
  const overlayRef = useRef<LearnOverlay | null>(null);
  const workerRef = useRef<Worker | null>(null);
  const jobState = useRef<JobState | null>(null);
  const req = useRef(0);

  useEffect(() => {
    const el = host.current;
    if (!el) return;
    const chart = createChart(el, learnChartOptions(theme));
    const colors = learnColors(theme);
    const series = chart.addSeries(CandlestickSeries, {
      upColor: colors.up,
      downColor: colors.down,
      wickUpColor: colors.up,
      wickDownColor: colors.down,
      borderVisible: false,
    });
    const overlay = new LearnOverlay();
    series.attachPrimitive(overlay);
    chartRef.current = chart;
    seriesRef.current = series;
    overlayRef.current = overlay;
    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      overlayRef.current = null;
      markersRef.current = null;
      linesRef.current = [];
    };
    // The chart is created once. Theme updates go through applyOptions.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    chartRef.current?.applyOptions(learnChartOptions(theme));
    const colors = learnColors(theme);
    seriesRef.current?.applyOptions({
      upColor: colors.up, downColor: colors.down, wickUpColor: colors.up, wickDownColor: colors.down,
    });
  }, [theme]);

  useEffect(() => {
    let worker: Worker | null = null;
    if (import.meta.env.MODE !== "test") {
      try {
        worker = new Worker(new URL("./learn.worker.ts", import.meta.url), { type: "module" });
        worker.onmessage = (ev: MessageEvent<{ id: number; read: LearnRead }>) => {
          if (ev.data.id === req.current) setRead(ev.data.read);
        };
      } catch {
        worker = null;
      }
    }
    workerRef.current = worker;
    return () => {
      worker?.terminate();
      workerRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (bars) {
      setCandles(bars);
      return;
    }
    const ac = new AbortController();
    const base = { symbol, timeframe, sessions: ["normal" as const], limit: 400 };
    const query = replay && cursor != null ? capCandlesQuery({ ...base, to: cursor }, cursor) : base;
    void fetchCandles(query, ac.signal)
      .then((page) => {
        if (ac.signal.aborted) return;
        const next = shownBars(page.candles.map(toBar), replay ? cursor : null);
        const series = seriesRef.current;
        series?.setData(next.map((bar) => ({
          time: bar.time as UTCTimestamp,
          open: bar.open ?? bar.close,
          high: bar.high,
          low: bar.low,
          close: bar.close,
        })));
        setCandles(next);
      })
      .catch(() => {
        if (!ac.signal.aborted) setCandles([]);
      });
    return () => ac.abort();
  }, [bars, symbol, timeframe, replay, cursor]);

  useEffect(() => {
    if (bars || replay) return;
    return subscribeLearnBars((msg) => {
      if (msg.symbol !== symbol || msg.timeframe !== timeframe) return;
      const incoming = msg.candles.map(toBar);
      const series = seriesRef.current;
      for (const bar of incoming) {
        series?.update({
          time: bar.time as UTCTimestamp,
          open: bar.open ?? bar.close,
          high: bar.high,
          low: bar.low,
          close: bar.close,
        });
      }
      setCandles((current) => shownBars(mergeBars(current, incoming), null));
    });
  }, [bars, replay, symbol, timeframe]);

  useEffect(() => {
    if (!replay) onLiveView?.({ symbol, timeframe, sessions: ["normal"], indicators: [] });
    else onLiveView?.(null);
    return () => onLiveView?.(null);
  }, [onLiveView, replay, symbol, timeframe]);

  useEffect(() => {
    const shown = shownBars(candles, replay ? cursor : null);
    const job = { bars: closedBars(shown, replay ? cursor : null), left, right, rangeSwings };
    const id = ++req.current;
    if (workerRef.current) {
      workerRef.current.postMessage({ id, job });
      return;
    }
    const done = runJob(job, jobState.current);
    jobState.current = done.state;
    setRead(done.read);
  }, [candles, cursor, left, rangeSwings, replay, right]);

  useEffect(() => {
    const series = seriesRef.current;
    const overlay = overlayRef.current;
    if (!series || !overlay || !read) return;
    const colors = learnColors(theme);
    for (const line of linesRef.current) series.removePriceLine(line);
    linesRef.current = togglesOn(read, toggles).map((level) => series.createPriceLine({
      price: level.price,
      title: level.label,
      color: colors.line,
      lineWidth: 1,
      axisLabelVisible: true,
    }));
    const markers: SeriesMarker<Time>[] = [];
    if (toggles.swings) {
      for (const swing of read.structure.swings) {
        markers.push({
          time: swing.time as UTCTimestamp,
          position: swing.kind === "high" ? "aboveBar" : "belowBar",
          color: colors.text,
          shape: "circle",
          text: swing.label,
        });
      }
    }
    if (!markersRef.current) markersRef.current = createSeriesMarkers(series);
    markersRef.current?.setMarkers(markers);
    overlay.setSegments(toggles.breaks ? read.structure.events.map((event) => ({
      from: event.fromTime,
      to: event.time,
      price: event.price,
      text: event.kind === "bos" ? "BOS" : "CHoCH",
      color: event.direction === "up" ? colors.up : colors.down,
    })) : []);
  }, [read, theme, toggles]);

  const labels = visibleLabels(read?.structure ?? null, read?.levels ?? [], toggles);
  const light = theme === "light";
  return (
    <div className={`flex h-full min-h-0 flex-col ${light ? "bg-white text-slate-900" : "bg-[#0c1016] text-slate-100"}`} data-testid="learn-pane">
      <div className="flex flex-wrap items-center gap-2 border-b border-black/10 px-3 py-2 text-xs">
        {SYMBOLS.map((item) => (
          <button key={item.id} type="button" className={chip(symbol === item.id)} onClick={() => setSymbol(item.id)}>{item.label}</button>
        ))}
        <span className="mx-1 h-4 w-px bg-black/10" />
        {TIMEFRAMES.map((tf) => (
          <button key={tf} type="button" className={chip(timeframe === tf)} onClick={() => setTimeframe(tf)}>{tf}</button>
        ))}
        <button type="button" className={chip(replay)} data-testid="learn-replay" onClick={() => {
          setReplay((on) => {
            const next = !on;
            setCursor(next ? Math.floor(Date.now() / 1000) : null);
            return next;
          });
        }}>Replay</button>
        {replay && cursor != null && (
          <>
            <input
              aria-label="Replay cursor"
              type="datetime-local"
              className="rounded border border-black/15 bg-transparent px-1"
              value={toLocalInput(cursor)}
              onChange={(e) => {
                const next = Math.floor(new Date(e.target.value).getTime() / 1000);
                if (Number.isFinite(next)) setCursor(next);
              }}
            />
            <button type="button" className={chip(false)} onClick={() => setCursor((value) => (value ?? 0) - (TF_SECONDS[timeframe] ?? 60))}>Back</button>
            <button type="button" className={chip(false)} onClick={() => setCursor((value) => (value ?? 0) + (TF_SECONDS[timeframe] ?? 60))}>Step</button>
          </>
        )}
        <button type="button" className={chip(theme === "dark")} data-testid="learn-theme" onClick={() => setTheme((mode) => mode === "light" ? "dark" : "light")}>
          {theme === "light" ? "Dark" : "Light"}
        </button>
        <label className="flex items-center gap-1">Pivot
          <input aria-label="Pivot left" type="number" min={1} max={10} className="w-12 rounded border border-black/15 bg-transparent px-1" value={left} onChange={(e) => setLeft(Number(e.target.value) || 1)} />
          <input aria-label="Pivot right" type="number" min={1} max={10} className="w-12 rounded border border-black/15 bg-transparent px-1" value={right} onChange={(e) => setRight(Number(e.target.value) || 1)} />
        </label>
        <label className="flex items-center gap-1">Range swings
          <input aria-label="Range swings" type="number" min={1} max={20} className="w-12 rounded border border-black/15 bg-transparent px-1" value={rangeSwings} onChange={(e) => setRangeSwings(Number(e.target.value) || 1)} />
        </label>
        <button type="button" className={`${chip(about)} ml-auto`} data-testid="learn-about" onClick={() => setAbout((open) => !open)}>About</button>
      </div>
      <div className="flex flex-wrap gap-1 px-3 py-1 text-[11px]">
        {LAYER_TOGGLES.map((layer) => (
          <button
            key={layer.key}
            type="button"
            aria-pressed={toggles[layer.key]}
            data-testid={`learn-toggle-${layer.key}`}
            className={chip(toggles[layer.key])}
            onClick={() => setToggles((current) => ({ ...current, [layer.key]: !current[layer.key] }))}
          >
            {layer.label}
          </button>
        ))}
        {read && (
          <span className="ml-2 rounded bg-black/5 px-2 py-0.5 font-semibold" title={explain(read.structure.trend)} data-testid="learn-trend">
            {read.structure.trend}
          </span>
        )}
      </div>
      <div ref={host} className="min-h-0 flex-1" data-testid="learn-chart" />
      {tip && <p className="px-3 py-1 text-xs" data-testid="learn-tip">{tip}</p>}
      <ul className="flex max-h-24 flex-wrap gap-2 overflow-auto px-3 py-1 text-[11px]" data-testid="learn-labels">
        {labels.map((label) => (
          <li key={label.id}>
            <button type="button" className="underline decoration-dotted" title={label.title} onMouseEnter={() => setTip(label.title)} onMouseLeave={() => setTip(null)}>
              {label.text}
            </button>
          </li>
        ))}
      </ul>
      {about && (
        <section className="border-t border-black/10 px-3 py-2 text-xs" data-testid="learn-credits">
          <h2 className="font-semibold">About</h2>
          <p className="mt-1">Charts: TradingView Lightweight Charts</p>
          <p className="mt-1">
            <a className="underline" href="https://www.apache.org/licenses/LICENSE-2.0" target="_blank" rel="noreferrer">Apache License 2.0</a>
            {" · "}
            <a className="underline" href="https://www.tradingview.com/lightweight-charts/" target="_blank" rel="noreferrer">library</a>
          </p>
          <p className="mt-1 text-black/60">Education only. No signals and no orders. The licence text is in frontend/src/learn/LIGHTWEIGHT_CHARTS_LICENSE.</p>
        </section>
      )}
    </div>
  );
}

function togglesOn(read: LearnRead, toggles: LayerToggles) {
  return read.levels.filter((level) => toggles[level.layer]);
}

function toLocalInput(epoch: number): string {
  const date = new Date(epoch * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function chip(on: boolean): string {
  return `rounded border px-2 py-0.5 ${on ? "border-sky-700 bg-sky-700/10" : "border-black/15"}`;
}
