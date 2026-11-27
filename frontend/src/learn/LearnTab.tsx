import { useEffect, useRef, useState } from "react";
import {
  CandlestickSeries,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type MouseEventParams,
  type UTCTimestamp,
} from "lightweight-charts";

import { capCandlesQuery } from "../replay/cap";
import { fetchCandles, type Candle, type Timeframe } from "../api/client";
import type { LiveView } from "../live/protocol";
import { subscribeLearnBars, useLiveStore } from "../store/liveStore";
import { explainCard, explainTip } from "./explainCards";
import { runJob, type JobState, type LearnRead } from "./job";
import { DEFAULT_LABEL_SWINGS } from "./labels";
import { pushLiveBar } from "./liveBars";
import { loadNotes, saveNotes } from "./notes";
import { LearnOverlay, type ChartMark } from "./overlay";
import { loadSignalSummary, runSignalReview, type SignalsSummary } from "./signalLog";
import { SignalsPanel } from "./SignalsPanel";
import { emptyBook } from "./signals";
import { closedBars, shownBars, type LearnBar } from "./structure";
import { DEFAULT_LAYERS, type StudyLayers, type StudyNote } from "./study";
import { learnChartOptions, learnColors, type LearnTheme } from "./theme";
import { fromIstInput, istClock, learnAxisTime, toIstInput } from "./time";
import "./learn.css";

const SYMBOLS = [
  { id: "NIFTY50", label: "NIFTY" },
  { id: "SENSEX", label: "SENSEX" },
  { id: "BANKNIFTY", label: "BANKNIFTY" },
] as const;

const TIMEFRAMES: Timeframe[] = ["1m", "3m", "5m", "15m", "1h", "1D"];
const TF_SECONDS: Record<string, number> = { "1m": 60, "3m": 180, "5m": 300, "15m": 900, "1h": 3600, "1D": 86400 };

const LAYER_GROUPS: { title: string; rows: { key: keyof StudyLayers; label: string }[] }[] = [
  { title: "AMD", rows: [{ key: "session", label: "Sessions" }, { key: "amd", label: "Accumulation, manipulation, distribution" }] },
  {
    title: "Price action",
    rows: [
      { key: "swing", label: "Swings" },
      { key: "structure", label: "BOS / CHoCH" },
      { key: "liquidity", label: "Liquidity and sweeps" },
      { key: "fvg", label: "Fair value gaps" },
      { key: "fvgFilled", label: "Show filled and older gaps" },
      { key: "ob", label: "Order blocks" },
      { key: "pattern", label: "Candle patterns at key levels" },
      { key: "patternAll", label: "Show all patterns" },
    ],
  },
  { title: "Context", rows: [{ key: "round", label: "Round numbers, 50 and 100" }, { key: "notes", label: "My notes" }] },
  { title: "Signals", rows: [{ key: "signals", label: "Signal boxes" }] },
];

function toBar(candle: Candle): LearnBar {
  return { time: candle.time, open: candle.open, high: candle.high, low: candle.low, close: candle.close };
}

function quoteOf(bars: readonly LearnBar[]): { price: string; range: string } | null {
  const last = bars[bars.length - 1];
  if (!last) return null;
  const day = istClock(last.time).day;
  const today = bars.filter((bar) => istClock(bar.time).day === day);
  const high = Math.max(...today.map((bar) => bar.high));
  const low = Math.min(...today.map((bar) => bar.low));
  const range = Math.round((high - low) * 100) / 100;
  return { price: last.close.toLocaleString("en-IN"), range: `${range} pts` };
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
  const [layers, setLayers] = useState<StudyLayers>(DEFAULT_LAYERS);
  const [left, setLeft] = useState(3);
  const [right, setRight] = useState(3);
  const [rangeSwings, setRangeSwings] = useState(4);
  const [labelSwings, setLabelSwings] = useState(DEFAULT_LABEL_SWINGS);
  const [replay, setReplay] = useState(false);
  const [cursor, setCursor] = useState<number | null>(null);
  const [candles, setCandles] = useState<LearnBar[]>(bars ?? []);
  const [read, setRead] = useState<LearnRead | null>(null);
  const [tab, setTab] = useState<"layers" | "events" | "explain" | "notes" | "signals">("layers");
  const [m5Extra, setM5Extra] = useState<LearnBar[]>([]);
  const [summary, setSummary] = useState<SignalsSummary | null>(null);
  const [reviewNote, setReviewNote] = useState<string | null>(null);
  const [picked, setPicked] = useState<ChartMark | null>(null);
  const [tip, setTip] = useState<string | null>(null);
  const [notes, setNotes] = useState<StudyNote[]>([]);
  const [reads, setReads] = useState<Record<string, string>>({});
  const [draft, setDraft] = useState("");
  const [readDraft, setReadDraft] = useState("");
  const [noteMode, setNoteMode] = useState(false);
  const [saved, setSaved] = useState<"file" | "browser" | null>(null);
  const host = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const overlayRef = useRef<LearnOverlay | null>(null);
  const workerRef = useRef<Worker | null>(null);
  const jobState = useRef<JobState | null>(null);
  const req = useRef(0);
  const candlesRef = useRef<LearnBar[]>(bars ?? []);
  const noteModeRef = useRef(false);
  const draftRef = useRef("");
  const link = useLiveStore((state) => state.link);
  const lastBarAt = useLiveStore((state) => state.lastBarAt);

  useEffect(() => { candlesRef.current = candles; }, [candles]);
  useEffect(() => { noteModeRef.current = noteMode; }, [noteMode]);
  useEffect(() => { draftRef.current = draft; }, [draft]);

  useEffect(() => {
    const el = host.current;
    if (!el) return;
    const chart = createChart(el, learnChartOptions(theme, timeframe));
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
    const onClick = (param: MouseEventParams) => {
      if (!param.point) return;
      if (noteModeRef.current) {
        const price = series.coordinateToPrice(param.point.y);
        const time = typeof param.time === "number" ? param.time : candlesRef.current.at(-1)?.time;
        const text = draftRef.current.trim();
        if (price != null && time != null && text) {
          addRef.current(time, price, text);
          setNoteMode(false);
        }
        return;
      }
      const hit = overlay.pick(param.point.x, param.point.y);
      if (!hit) return;
      setPicked(hit);
      setTab("explain");
    };
    const onMove = (param: MouseEventParams) => {
      if (!param.point) {
        setTip(null);
        return;
      }
      const hit = overlay.pick(param.point.x, param.point.y);
      setTip(hit ? explainTip(hit.kind) : null);
    };
    chart.subscribeClick(onClick);
    chart.subscribeCrosshairMove(onMove);
    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      overlayRef.current = null;
    };
    // The chart is created once. Theme and timeframe updates go through applyOptions.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    chartRef.current?.applyOptions(learnChartOptions(theme, timeframe));
    const colors = learnColors(theme);
    seriesRef.current?.applyOptions({
      upColor: colors.up, downColor: colors.down, wickUpColor: colors.up, wickDownColor: colors.down,
    });
  }, [theme, timeframe]);

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
    let gone = false;
    void loadNotes().then((file) => {
      if (gone) return;
      setNotes(file.notes);
      setReads(file.reads);
    });
    return () => { gone = true; };
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
        seriesRef.current?.setData(next.map((bar) => ({
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
      const next = pushLiveBar(candlesRef.current, incoming, (bar) => {
        series?.update({
          time: bar.time as UTCTimestamp,
          open: bar.open ?? bar.close,
          high: bar.high,
          low: bar.low,
          close: bar.close,
        });
      }, (bar) => bar.time);
      setCandles(shownBars(next, null));
    });
  }, [bars, replay, symbol, timeframe]);

  useEffect(() => {
    if (!replay) onLiveView?.({ symbol, timeframe, sessions: ["normal"], indicators: [] });
    else onLiveView?.(null);
    return () => onLiveView?.(null);
  }, [onLiveView, replay, symbol, timeframe]);

  useEffect(() => {
    if (timeframe === "5m" || bars) return;
    const ac = new AbortController();
    const base = { symbol, timeframe: "5m" as const, sessions: ["normal" as const], limit: 800 };
    const query = replay && cursor != null ? capCandlesQuery({ ...base, to: cursor }, cursor) : base;
    void fetchCandles(query, ac.signal)
      .then((page) => {
        if (ac.signal.aborted) return;
        setM5Extra(shownBars(page.candles.map(toBar), replay ? cursor : null));
      })
      .catch(() => {
        if (!ac.signal.aborted) setM5Extra([]);
      });
    return () => ac.abort();
  }, [bars, cursor, replay, symbol, timeframe]);

  useEffect(() => {
    let gone = false;
    void loadSignalSummary().then((file) => {
      if (!gone) setSummary(file);
    });
    return () => { gone = true; };
  }, []);

  useEffect(() => {
    const shown = shownBars(candles, replay ? cursor : null);
    const signalSource = timeframe === "5m" ? shown : shownBars(m5Extra, replay ? cursor : null);
    const job = {
      bars: closedBars(shown, replay ? cursor : null),
      left,
      right,
      rangeSwings,
      timeframe,
      symbol,
      now: shown.at(-1)?.time ?? 0,
      m5: closedBars(signalSource, replay ? cursor : null),
      signalNow: (replay && cursor != null ? cursor : signalSource.at(-1)?.time) ?? 0,
    };
    const id = ++req.current;
    if (workerRef.current) {
      workerRef.current.postMessage({ id, job });
      return;
    }
    const done = runJob(job, jobState.current);
    jobState.current = done.state;
    setRead(done.read);
  }, [candles, cursor, left, m5Extra, rangeSwings, replay, right, symbol, timeframe]);

  useEffect(() => {
    const overlay = overlayRef.current;
    if (!overlay || !read) return;
    overlay.setStudy({
      study: read.study,
      layers,
      limit: labelSwings,
      notes: notes.filter((note) => note.symbol === symbol),
      selected: picked?.id ?? null,
      signals: read.signals.signals,
    });
  }, [labelSwings, layers, notes, picked, read, symbol]);

  const dayKey = istClock((replay && cursor != null ? cursor : candles.at(-1)?.time) ?? Math.floor(Date.now() / 1000)).day;
  useEffect(() => { setReadDraft(reads[dayKey] ?? ""); }, [dayKey, reads]);

  const persist = (nextNotes: StudyNote[], nextReads: Record<string, string>) => {
    void saveNotes({ notes: nextNotes, reads: nextReads }).then(setSaved);
  };
  const addRef = useRef<(time: number, price: number, text: string) => void>(() => {});
  addRef.current = (time, price, text) => {
    const note: StudyNote = { id: `${symbol}-${time}-${notes.length}`, time, price, text, symbol };
    const next = [...notes, note];
    setNotes(next);
    setDraft("");
    persist(next, reads);
  };

  const quote = quoteOf(shownBars(candles, replay ? cursor : null));
  const banner = candles.length === 0 ? "Waiting for candles." : (read?.study.banner ?? "Waiting for candles.");
  const warn = /failed|patient|No AMD|Weekend|Before the open|not enough/.test(banner);
  const tick = lastBarAt != null
    ? learnAxisTime(Math.floor(lastBarAt / 1000))
    : candles.at(-1) ? learnAxisTime(candles[candles.length - 1]!.time) : null;
  const feed = replay ? "replay" : link === "open" ? "live" : "feed down";
  const card = picked ? explainCard(picked.kind) : null;
  const visibleNotes = notes.filter((note) => note.symbol === symbol);

  return (
    <div className={`learn${theme === "dark" ? " learn-dark" : ""}`} data-testid="learn-pane">
      <header>
        <span className="brand">TradeBoss <em>Learn</em></span>
        <div className="sym seg">
          {SYMBOLS.map((item) => (
            <button key={item.id} type="button" aria-pressed={symbol === item.id} onClick={() => setSymbol(item.id)}>{item.label}</button>
          ))}
        </div>
        <div className="seg">
          {TIMEFRAMES.map((tf) => (
            <button key={tf} type="button" aria-pressed={timeframe === tf} onClick={() => setTimeframe(tf)}>{tf}</button>
          ))}
        </div>
        <button type="button" className="ghost" aria-pressed={!replay} onClick={() => { setReplay(false); setCursor(null); }}>Live</button>
        <button type="button" className="ghost" aria-pressed={replay} data-testid="learn-replay" onClick={() => {
          setReplay(true);
          setCursor(Math.floor(Date.now() / 1000));
        }}>Replay</button>
        <div className="head-right">
          <span className="status">
            <i className={`dot ${feed === "live" ? "live" : feed === "replay" ? "replay" : ""}`} />
            {feed}{tick ? ` · ${tick} IST` : ""}
          </span>
          {quote && <span className="quote">{quote.price}<small>range {quote.range}</small></span>}
          <button type="button" className="ghost" data-testid="learn-theme" onClick={() => setTheme((mode) => mode === "light" ? "dark" : "light")}>
            {theme === "light" ? "Dark" : "Light"}
          </button>
        </div>
      </header>
      <div className={`banner${warn ? " warn" : ""}`} data-testid="learn-banner">{banner}</div>
      {replay && cursor != null && (
        <div className="replaybar">
          <input aria-label="Replay cursor" type="datetime-local" value={toIstInput(cursor)} onChange={(e) => {
            const next = fromIstInput(e.target.value);
            if (next != null) setCursor(next);
          }} />
          <button type="button" className="ghost" onClick={() => setCursor((value) => (value ?? 0) - (TF_SECONDS[timeframe] ?? 60))}>Back</button>
          <button type="button" className="ghost" onClick={() => setCursor((value) => (value ?? 0) + (TF_SECONDS[timeframe] ?? 60))}>Step</button>
        </div>
      )}
      <div className="body">
        <div className="chartwrap">
          <div ref={host} className="chart" data-testid="learn-chart" />
          {tip && <div className="tip" data-testid="learn-tip">{tip}</div>}
        </div>
        <aside>
          <div className="tabs" role="tablist">
            {(["layers", "events", "explain", "notes", "signals"] as const).map((name) => (
              <button key={name} type="button" role="tab" aria-selected={tab === name} onClick={() => setTab(name)}>
                {name[0]!.toUpperCase() + name.slice(1)}
              </button>
            ))}
          </div>
          <div className="panel">
            {tab === "layers" && (
              <>
                {LAYER_GROUPS.map((group) => (
                  <section key={group.title} className="group">
                    <h3>{group.title}</h3>
                    {group.rows.map((row) => (
                      <label key={row.key} className="row">
                        <input
                          type="checkbox"
                          checked={layers[row.key]}
                          data-testid={`learn-layer-${row.key}`}
                          onChange={() => setLayers((current) => ({ ...current, [row.key]: !current[row.key] }))}
                        />
                        {row.label}
                      </label>
                    ))}
                  </section>
                ))}
                <section className="group">
                  <h3>Chart</h3>
                  <label className="row">Labels shown
                    <input aria-label="Labels shown" type="number" min={1} max={40} value={labelSwings} onChange={(e) => setLabelSwings(Number(e.target.value) || 1)} />
                  </label>
                  <label className="row">Pivot
                    <input aria-label="Pivot left" type="number" min={1} max={10} value={left} onChange={(e) => setLeft(Number(e.target.value) || 1)} />
                    <input aria-label="Pivot right" type="number" min={1} max={10} value={right} onChange={(e) => setRight(Number(e.target.value) || 1)} />
                  </label>
                  <label className="row">Range swings
                    <input aria-label="Range swings" type="number" min={1} max={20} value={rangeSwings} onChange={(e) => setRangeSwings(Number(e.target.value) || 1)} />
                  </label>
                </section>
              </>
            )}
            {tab === "events" && (
              <div>
                {(read?.study.events ?? []).map((event, index) => (
                  <button
                    key={`${event.time}-${event.text}-${index}`}
                    type="button"
                    className="event"
                    data-testid="learn-event"
                    onClick={() => {
                      setPicked({ id: `event-${index}`, kind: event.kind, detail: event.detail, time: event.time });
                      setTab("explain");
                    }}
                  >
                    <i style={{ background: event.color }} />
                    <span>
                      <b>{learnAxisTime(event.time)} IST</b>
                      <br />
                      {event.text}
                    </span>
                  </button>
                ))}
                {(read?.study.events.length ?? 0) === 0 && <p>No events on the closed candles yet.</p>}
              </div>
            )}
            {tab === "explain" && (
              card && picked ? (
                <article className="card" data-testid="learn-explain">
                  <h3>{card.title}</h3>
                  <div className="box"><b>On this chart</b><p>{picked.detail}</p><p>{learnAxisTime(picked.time)} IST</p></div>
                  <div className="box"><b>What it is</b><p>{card.what}</p></div>
                  <div className="box"><b>Why it matters</b><p>{card.why}</p></div>
                  <div className="box"><b>What to watch next</b><p>{card.next}</p></div>
                </article>
              ) : <p>Click a label, a line, a box, or an event.</p>
            )}
            {tab === "signals" && (
              <SignalsPanel
                symbol={symbol}
                book={read?.signals ?? emptyBook()}
                summary={summary}
                reviewNote={reviewNote}
                onReview={() => {
                  setReviewNote("Running the review…");
                  void runSignalReview()
                    .then((file) => {
                      setSummary(file);
                      setReviewNote("Review saved under data/learn/signals.");
                    })
                    .catch(() => setReviewNote("The review could not run. Start the app and try again."));
                }}
              />
            )}
            {tab === "notes" && (
              <div>
                <label className="row">Note
                  <input type="text" aria-label="Note text" value={draft} onChange={(e) => setDraft(e.target.value)} />
                </label>
                <p>
                  <button type="button" className="ghost" onClick={() => setNoteMode((on) => !on)}>
                    {noteMode ? "Click the chart…" : "Add on the chart"}
                  </button>
                  {" "}
                  <button type="button" className="ghost" onClick={() => {
                    const last = candles.at(-1);
                    if (last && draft.trim()) addRef.current(last.time, last.close, draft.trim());
                  }}>Add on last candle</button>
                </p>
                <ul>
                  {visibleNotes.map((note) => (
                    <li key={note.id}>{learnAxisTime(note.time)} · {note.price} · {note.text}</li>
                  ))}
                </ul>
                <label>My read of today
                  <textarea aria-label="My read of today" rows={5} value={readDraft} onChange={(e) => setReadDraft(e.target.value)} onBlur={() => {
                    const next = { ...reads, [dayKey]: readDraft };
                    setReads(next);
                    persist(notes, next);
                  }} />
                </label>
                {saved && <p>{saved === "file" ? "Saved in data/learn/notes." : "Saved in this browser. The notes file was unreachable."}</p>}
              </div>
            )}
          </div>
        </aside>
      </div>
      <footer data-testid="learn-credits">
        Read-only study chart — never places trades. Charts:{" "}
        <a href="https://www.tradingview.com/lightweight-charts/">TradingView Lightweight Charts™</a> (Apache 2.0).
      </footer>
    </div>
  );
}
