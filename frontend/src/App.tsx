import { useCallback, useEffect, useRef, useState } from "react";

import type { SeriesMarker, Time, UTCTimestamp } from "lightweight-charts";

import { ChartView } from "./chart/ChartView";
import { DrawSync, DrawToolbar } from "./draw/toolbar";
import { TimeframeBar } from "./chart/TimeframeBar";
import { AnalysisPanel } from "./panels/AnalysisPanel";
import { BacktestPanel } from "./panels/BacktestPanel";
import { PineAlertsPanel } from "./panels/PineAlertsPanel";
import { PinePanel } from "./panels/PinePanel";
import { pineMarkers } from "./pine/markers";
import { usePineLiveHost } from "./pine/liveHost";
import { usePineLiveStore } from "./store/pineLiveStore";
import { BacktestResults, TradeCard } from "./panels/BacktestResults";
import { IndicatorsMenu } from "./panels/IndicatorsMenu";
import { DataTokenBadge } from "./panels/DataTokenBadge";
import { LiveBadge } from "./panels/LiveBadge";
import { SnapshotBadge } from "./panels/SnapshotBadge";
import { PaperPanel } from "./panels/PaperPanel";
import { PaperTrades } from "./panels/PaperTrades";
import { PaperDashboard } from "./paper/PaperDashboard";
import { SectionBoundary } from "./paper/SectionBoundary";
import { usePaperTradesStore } from "./store/paperTradesStore";
import { paperMarkers, type PaperSignal } from "./paper/present";
import { PAPER_TAB_KEY, readJson, writeJson } from "./paper/storage";
import { usePaperStore } from "./store/paperStore";
import { usePaperAlerts } from "./paper/useAlerts";
import { SessionsMenu } from "./panels/SessionsMenu";
import { SymbolSelector } from "./panels/SymbolSelector";
import { ReplayBar, useReplayClock } from "./replay/ReplayBar";
import { reveal } from "./replay/cursor";
import { useReplayStore } from "./replay/store";
import { useBackendStore, type BackendStatus } from "./store/backendStore";
import { useBacktestStore } from "./store/backtestStore";
import { useChartStore } from "./store/chartStore";
import { scopeKey } from "./indicators/cache";
import { useIndicatorStore } from "./store/indicatorStore";
import { liveView, useLiveStore } from "./store/liveStore";
import type { LevelLine } from "./ai/present";
import { useGexStore } from "./store/gexStore";
import { useMarketViewStore } from "./store/marketViewStore";
import { MarketViewBox, MarketViewPanel } from "./panels/MarketViewPanel";
import { viewLevels } from "./marketView/present";
import { gexLevels, gexSummary } from "./chart/gex";
import { OptionsTab } from "./options/OptionsTab";
import { SensexPanel } from "./panels/SensexPanel";
import { ScannerPanel } from "./panels/ScannerPanel";
import { holdLeader } from "./api/sharedPoll";
import { ChartScopeProvider, NIFTY_SCOPE } from "./store/chartScope";
import { useSensexChartStore } from "./store/chartStore";
import { useSensexIndicatorStore } from "./store/indicatorStore";
import { chartHidden, type MainView } from "./chart/controls";
import type { PatternShade } from "./chart/similarShade";
import { SimilarPanel } from "./panels/SimilarPanel";
import { fetchPredictions, istDay, type CandleMatch, type SimilarTimeframe } from "./api/similar";
import { forwardEndTime, snapshotMarkers } from "./similar/present";

const POLL_INTERVAL_MS = 3000;
const NO_PAPER_SIGNALS: PaperSignal[] = [];
/** wait for typing in a parameter box to settle before asking the backend */
const INDICATOR_DEBOUNCE_MS = 200;
/** the backend caches the option chain for 30 s; one minute is plenty for GEX */
const GEX_POLL_MS = 60_000;
/** the view is built at 08:50 IST; re-read it every 5 minutes so an open page picks it up */
const MARKET_VIEW_POLL_MS = 5 * 60_000;

const STATUS_UI: Record<BackendStatus, { label: string; dot: string; text: string }> = {
  checking: { label: "checking backend…", dot: "bg-yellow-400", text: "text-yellow-300" },
  connected: { label: "backend connected", dot: "bg-emerald-400", text: "text-emerald-300" },
  disconnected: { label: "backend disconnected", dot: "bg-red-500", text: "text-red-400" },
};

export default function App() {
  const [mainView, setMainView] = useState<MainView>(() => {
    const saved = readJson<string>(PAPER_TAB_KEY, "chart");
    return saved === "options" || saved === "paper" || saved === "chart" || saved === "sensex" || saved === "scanner" ? saved : "chart";
  });
  const setView = useCallback((view: MainView) => {
    setMainView(view);
    writeJson(PAPER_TAB_KEY, view);
  }, []);
  const backendStatus = useBackendStore((s) => s.status);
  const poll = useBackendStore((s) => s.poll);

  const symbols = useChartStore((s) => s.symbols);
  const symbol = useChartStore((s) => s.symbol);
  const sessions = useChartStore((s) => s.sessions);
  const timeframe = useChartStore((s) => s.timeframe);
  const candles = useChartStore((s) => s.candles);
  const loaded = useChartStore((s) => s.loaded);
  const loadingOlder = useChartStore((s) => s.loadingOlder);
  const loadingNewer = useChartStore((s) => s.loadingNewer);
  const loadOlder = useChartStore((s) => s.loadOlder);
  const loadNewer = useChartStore((s) => s.loadNewer);
  const status = useChartStore((s) => s.status);
  const error = useChartStore((s) => s.error);
  const init = useChartStore((s) => s.init);
  const setTimeframe = useChartStore((s) => s.setTimeframe);
  const indicatorItems = useIndicatorStore((s) => s.items);
  const refreshIndicators = useIndicatorStore((s) => s.refresh);

  useEffect(() => {
    return holdLeader("tradeboss-health", () => {
      const controller = new AbortController();
      void poll(controller.signal);
      const id = window.setInterval(() => void poll(controller.signal), POLL_INTERVAL_MS);
      return () => {
        controller.abort();
        window.clearInterval(id);
      };
    });
  }, [poll]);

  // (Re)load chart data once the backend is reachable.
  useEffect(() => {
    if (backendStatus === "connected" && symbols.length === 0 && status !== "loading") {
      void init();
    }
  }, [backendStatus, symbols.length, status, init]);

  // Fetch whatever indicator values the loaded candles still lack (the store skips anything cached).
  // Edits to indicator settings are debounced (typing a parameter); new candles are fetched at once.
  const lastItems = useRef(indicatorItems);
  useEffect(() => {
    if (!loaded) return;
    const settingsChanged = lastItems.current !== indicatorItems;
    lastItems.current = indicatorItems;
    const run = (): void =>
      void refreshIndicators({
        symbol: loaded.symbol,
        timeframe: loaded.timeframe,
        sessions: loaded.sessions,
        candles,
        items: indicatorItems,
      });
    if (!settingsChanged) {
      run();
      return;
    }
    const id = window.setTimeout(run, INDICATOR_DEBOUNCE_MS);
    return () => window.clearTimeout(id);
  }, [loaded, candles, indicatorItems, refreshIndicators]);

  // Live feed: one WebSocket to our own backend (it owns the single Upstox connection).
  const startLive = useLiveStore((s) => s.start);
  const stopLive = useLiveStore((s) => s.stop);
  const setLiveViews = useLiveStore((s) => s.setViews);
  useEffect(() => {
    startLive();
    return () => stopLive();
  }, [startLive, stopLive]);

  // Nifty + Sensex both stay subscribed so opening the Sensex tab never drops Nifty live bars.
  const sensexLoaded = useSensexChartStore((s) => s.loaded);
  const sensexIndicatorItems = useSensexIndicatorStore((s) => s.items);
  const loadedSymbol = loaded?.symbol;
  const loadedTimeframe = loaded?.timeframe;
  const loadedSessions = loaded?.sessions;
  const sensexSymbol = sensexLoaded?.symbol;
  const sensexTimeframe = sensexLoaded?.timeframe;
  const sensexSessions = sensexLoaded?.sessions;
  useEffect(() => {
    const views = [
      liveView(
        loadedSymbol && loadedTimeframe && loadedSessions
          ? { symbol: loadedSymbol, timeframe: loadedTimeframe, sessions: loadedSessions }
          : null,
        indicatorItems,
      ),
      liveView(
        sensexSymbol && sensexTimeframe && sensexSessions
          ? { symbol: sensexSymbol, timeframe: sensexTimeframe, sessions: sensexSessions }
          : null,
        sensexIndicatorItems,
      ),
    ].filter((v): v is NonNullable<typeof v> => v != null);
    setLiveViews(views);
  }, [
    loadedSymbol,
    loadedTimeframe,
    loadedSessions,
    indicatorItems,
    sensexSymbol,
    sensexTimeframe,
    sensexSessions,
    sensexIndicatorItems,
    setLiveViews,
  ]);

  const info = symbols.find((s) => s.symbol === symbol);
  const ui = STATUS_UI[backendStatus];
  const panelOpen = useBacktestStore((s) => s.panelOpen);
  const replayActive = useReplayStore((s) => s.active);
  const gexEnabled = useGexStore((s) => s.enabled);
  const gexData = useGexStore((s) => s.data);
  const gexError = useGexStore((s) => s.error);
  const setGexEnabled = useGexStore((s) => s.setEnabled);
  const loadGex = useGexStore((s) => s.load);
  const gexOn = gexEnabled && symbol === "NIFTY50" && !replayActive;
  useEffect(() => {
    if (!gexOn) return;
    const ac = new AbortController();
    void loadGex(ac.signal);
    const id = setInterval(() => void loadGex(ac.signal), GEX_POLL_MS);
    return () => {
      clearInterval(id);
      ac.abort();
    };
  }, [gexOn, loadGex]);
  const gexShown = gexOn && gexData?.available ? gexData : null;
  const marketView = useMarketViewStore((s) => s.view);
  const marketPanelOpen = useMarketViewStore((s) => s.panelOpen);
  const setMarketPanelOpen = useMarketViewStore((s) => s.setPanelOpen);
  const marketLevelsOn = useMarketViewStore((s) => s.showLevels);
  const loadMarketView = useMarketViewStore((s) => s.load);
  useEffect(() => {
    const ac = new AbortController();
    void loadMarketView(false, ac.signal);
    const id = setInterval(() => void loadMarketView(false, ac.signal), MARKET_VIEW_POLL_MS);
    return () => {
      clearInterval(id);
      ac.abort();
    };
  }, [loadMarketView]);
  const marketLevels =
    marketView && marketLevelsOn && symbol === "NIFTY50" && !replayActive ? viewLevels(marketView.levels) : [];
  const replayCursorTime = useReplayStore((s) => s.cursor);
  useReplayClock();
  usePaperAlerts();
  usePineLiveHost();
  const setPanelOpen = useBacktestStore((s) => s.setPanelOpen);
  const [pineOpen, setPineOpen] = useState(false);
  const [pineAlertsOpen, setPineAlertsOpen] = useState(false);
  const pineScripts = usePineLiveStore((s) => s.scripts);
  const pineMarkersByScript = usePineLiveStore((s) => s.markersByScript);
  const pineNiftySignals = pineScripts
    .filter((s) => s.enabled && s.scope === "nifty")
    .flatMap((s) => pineMarkersByScript[s.id] ?? []);
  const [analysisOpen, setAnalysisOpen] = useState(false);
  const [requestToken, setRequestToken] = useState(0);
  const [analysisLevels, setAnalysisLevels] = useState<LevelLine[]>([]);
  const [paperOpen, setPaperOpen] = useState(false);
  const onAnalysisLevels = useCallback((lines: LevelLine[]) => setAnalysisLevels(lines), []);
  useEffect(() => {
    setAnalysisLevels([]);
  }, [symbol]);
  const activeRun = useBacktestStore((s) => s.active);
  const focus = useBacktestStore((s) => s.focus);
  const selectTrade = useBacktestStore((s) => s.selectTrade);
  const showAround = useChartStore((s) => s.showAround);
  const markers: SeriesMarker<Time>[] = [];
  const paperStatus = usePaperStore((s) => s.status);
  const replayReveal = replayActive && replayCursorTime != null
    ? reveal(
        (activeRun?.result?.trades ?? []).map((trade) => ({
          id: trade.id,
          entry_time: trade.entry_time,
          exit_time: trade.exit_time,
          stop: null,
        })),
        replayCursorTime,
      )
    : null;
  const snapBar = (time: number): number => {
    let snapped = time;
    for (const candle of candles) {
      if (candle.time <= time) snapped = candle.time;
      else break;
    }
    return snapped;
  };
  for (const mark of replayReveal ? replayReveal.markers : []) {
    const trade = activeRun?.result?.trades.find((item) => item.id === mark.id);
    const long = trade?.direction === "LONG";
    markers.push({
      time: snapBar(mark.time) as UTCTimestamp,
      position: mark.kind === "entry" && long ? "belowBar" : "aboveBar",
      shape: mark.kind === "exit" ? "circle" : long ? "arrowUp" : "arrowDown",
      color: mark.kind === "exit" ? "#94a3b8" : long ? "#26a69a" : "#ef5350",
      text: mark.kind === "entry" ? String(mark.id) : undefined,
    });
  }
  if (!replayReveal) {
    for (const trade of activeRun?.result?.trades ?? []) {
      const long = trade.direction === "LONG";
      markers.push({
        time: trade.entry_time as UTCTimestamp,
        position: long ? "belowBar" : "aboveBar",
        shape: long ? "arrowUp" : "arrowDown",
        color: long ? "#26a69a" : "#ef5350",
        text: String(trade.id),
      });
      markers.push({
        time: trade.exit_time as UTCTimestamp,
        position: "aboveBar",
        shape: "circle",
        color: "#94a3b8",
      });
    }
  }
  const replayCard = replayReveal?.cardId == null
    ? null
    : activeRun?.result?.trades.find((trade) => trade.id === replayReveal.cardId);
  if ((symbol === "NIFTY50" || symbol === "SENSEX") && !replayActive && paperOpen) {
    const times = candles.map((c) => c.time);
    for (const slot of paperStatus?.slots ?? []) {
      const slotSym = (slot.params as { symbol?: string } | undefined)?.symbol ?? "NIFTY50";
      if (symbol === "SENSEX" ? slotSym !== "SENSEX" : slotSym === "SENSEX") continue;
      markers.push(...paperMarkers(slot.signals ?? NO_PAPER_SIGNALS, times, slot.slot === "1" ? "" : `${slot.slot}·`));
    }
  }
  const tradeOverlay = usePaperTradesStore((s) => s.overlay);
  const paperFocus = usePaperTradesStore((s) => s.focus);
  const [similarOpen, setSimilarOpen] = useState(false);
  const [patternShade, setPatternShade] = useState<PatternShade | null>(null);
  const [similarFocus, setSimilarFocus] = useState<{ token: number; time: number } | null>(null);
  const [predictionArrows, setPredictionArrows] = useState(true);
  const [snapshots, setSnapshots] = useState<{ query: { end: number }; direction: "up" | "down" | "none"; up_count: number; match_n: number; kind: string }[]>([]);
  const snapshotDay = replayActive && replayCursorTime != null ? istDay(replayCursorTime) : istDay();
  useEffect(() => {
    if (!symbol || !["NIFTY50", "SENSEX", "BANKNIFTY"].includes(symbol)) {
      setSnapshots([]);
      return;
    }
    const controller = new AbortController();
    const load = () => {
      void fetchPredictions(symbol, snapshotDay, controller.signal)
        .then((rows) => setSnapshots(rows))
        .catch((err: unknown) => {
          if (err instanceof DOMException && err.name === "AbortError") return;
        });
    };
    load();
    const id = window.setInterval(load, 60_000);
    return () => {
      controller.abort();
      window.clearInterval(id);
    };
  }, [symbol, snapshotDay]);
  const chartFocus = [focus, paperFocus, similarFocus].reduce<{ token: number; time: number } | null>(
    (best, item) => (!item ? best : !best || item.token > best.token ? item : best),
    null,
  );
  const openSimilarMatch = useCallback((match: CandleMatch, tf: SimilarTimeframe) => {
    void (async () => {
      const chart = useChartStore.getState();
      if (chart.timeframe !== tf) await chart.setTimeframe(tf);
      await useChartStore.getState().showRange(match.start, match.end);
      setPatternShade({ start: match.start, end: match.end, forwardEnd: match.end });
      setSimilarFocus({ token: Date.now(), time: match.start });
    })();
  }, []);
  const revealNext = useCallback((target: { start: number; end: number; timeframe: SimilarTimeframe }, count: number) => {
    return (async (): Promise<"shown" | "waiting"> => {
      const chart = useChartStore.getState();
      if (chart.timeframe !== target.timeframe) await chart.setTimeframe(target.timeframe);
      const bar = target.timeframe === "1D" ? 86_400 : target.timeframe === "1h" ? 3_600 : Number(target.timeframe.replace("m", "")) * 60;
      const replayTo = useReplayStore.getState().active ? useReplayStore.getState().cursor : null;
      const limit = replayTo ?? Math.floor(Date.now() / 1000) - bar;
      await useChartStore.getState().showRange(target.start, target.end + count * bar);
      const times = useChartStore.getState().candles.map((candle) => candle.time);
      const forward = forwardEndTime(times, target.end, count, limit);
      if (forward == null) return "waiting";
      setPatternShade({ start: target.start, end: target.end, forwardEnd: forward });
      setSimilarFocus({ token: Date.now(), time: target.start });
      return "shown";
    })();
  }, []);
  if ((symbol === "NIFTY50" || symbol === "SENSEX") && tradeOverlay) {
    for (const m of tradeOverlay.markers) {
      markers.push({
        time: snapBar(m.time) as UTCTimestamp,
        position: m.kind === "entry" ? (m.long ? "belowBar" : "aboveBar") : m.long ? "aboveBar" : "belowBar",
        shape: m.kind === "exit" ? "circle" : m.long ? "arrowUp" : "arrowDown",
        color: m.kind === "exit" ? "#e2e8f0" : m.long ? "#26a69a" : "#ef5350",
        text: m.kind === "entry" ? "entry" : "exit",
      });
    }
  }
  if (predictionArrows) {
    for (const mark of snapshotMarkers(snapshots)) {
      markers.push({
        time: mark.time as UTCTimestamp,
        position: mark.direction === "down" ? "belowBar" : "aboveBar",
        shape: mark.direction === "up" ? "arrowUp" : mark.direction === "down" ? "arrowDown" : "circle",
        color: mark.direction === "up" ? "#26a69a" : mark.direction === "down" ? "#ef5350" : "#94a3b8",
        text: mark.text,
      });
    }
  }
  markers.push(...pineMarkers(pineNiftySignals, candles.map((c) => c.time)));
  markers.sort((a, b) => Number(a.time) - Number(b.time));
  const pickTime = (time: number): void => {
    const trade = activeRun?.result?.trades.find((item) => item.entry_time === time || item.exit_time === time);
    if (!trade) return;
    void showAround(trade.entry_time).then(() => selectTrade(trade.id, trade.entry_time));
  };

  return (
    <div className="flex h-full flex-col">
      <header className="flex min-h-11 shrink-0 flex-wrap items-center gap-4 border-b border-white/10 px-3 py-1">
        <span className="text-sm font-semibold tracking-tight">TradeBoss</span>
        <div className="flex items-center gap-1 rounded border border-white/10 p-0.5" data-testid="main-view-tabs">
          <button
            type="button"
            className={`rounded px-2.5 py-1 text-xs ${
              mainView === "chart" ? "bg-white/10 text-white" : "text-white/55 hover:text-white/80"
            }`}
            onClick={() => setView("chart")}
            data-testid="tab-chart"
          >
            Chart
          </button>
          <button
            type="button"
            className={`rounded px-2.5 py-1 text-xs ${
              mainView === "options" ? "bg-white/10 text-white" : "text-white/55 hover:text-white/80"
            }`}
            onClick={() => setView("options")}
            data-testid="tab-options"
          >
            Options
          </button>
          <button
            type="button"
            className={`rounded px-2.5 py-1 text-xs ${
              mainView === "paper" ? "bg-white/10 text-white" : "text-white/55 hover:text-white/80"
            }`}
            onClick={() => setView("paper")}
            data-testid="tab-paper"
          >
            Paper
          </button>
          <button
            type="button"
            className={`rounded px-2.5 py-1 text-xs ${
              mainView === "sensex" ? "bg-white/10 text-white" : "text-white/55 hover:text-white/80"
            }`}
            onClick={() => setView("sensex")}
            data-testid="tab-sensex"
          >
            SENSEX
          </button>
          <button
            type="button"
            className={`rounded px-2.5 py-1 text-xs ${
              mainView === "scanner" ? "bg-white/10 text-white" : "text-white/55 hover:text-white/80"
            }`}
            onClick={() => setView("scanner")}
            data-testid="tab-scanner"
          >
            Stock Scanner
          </button>
        </div>
        {mainView === "chart" ? (
          <>
        <SymbolSelector />
        <TimeframeBar info={info} selected={timeframe} onSelect={(tf) => void setTimeframe(tf)} />
        <SessionsMenu />
        <IndicatorsMenu />
        <button
          type="button"
          className="rounded border border-white/15 px-2 py-1 text-xs text-white/80"
          onClick={() => setPanelOpen(!panelOpen)}
        >
          Backtest
        </button>
        <ReplayBar />
        <button
          type="button"
          className="rounded border border-white/15 px-2 py-1 text-xs text-white/80"
          onClick={() => setPineOpen(!pineOpen)}
        >
          Pine
        </button>
        <button
          type="button"
          className="rounded border border-white/15 px-2 py-1 text-xs text-white/80"
          onClick={() => setPineAlertsOpen(!pineAlertsOpen)}
          data-testid="pine-alerts-toggle"
        >
          Alerts
        </button>
        <button
          type="button"
          className="rounded border border-white/15 px-2 py-1 text-xs text-white/80"
          onClick={() => {
            setAnalysisOpen(true);
            setRequestToken((token) => token + 1);
          }}
        >
          Analyse
        </button>
        <button
          type="button"
          className={`rounded border px-2 py-1 text-xs ${
            similarOpen ? "border-sky-400/60 bg-sky-400/10 text-sky-200" : "border-white/15 text-white/80"
          }`}
          onClick={() => setSimilarOpen((open) => !open)}
          data-testid="similar-toggle"
        >
          Similar
        </button>
        <button
          type="button"
          className={`rounded border px-2 py-1 text-xs ${
            predictionArrows ? "border-emerald-400/50 text-emerald-200" : "border-white/15 text-white/50"
          }`}
          onClick={() => setPredictionArrows((on) => !on)}
          data-testid="prediction-arrows"
        >
          Arrows
        </button>
        <button
          type="button"
          className={`rounded border px-2 py-1 text-xs ${
            paperOpen ? "border-sky-400/60 bg-sky-400/10 text-sky-200" : "border-white/15 text-white/80"
          }`}
          onClick={() => setPaperOpen((open) => !open)}
          data-testid="paper-side-toggle"
        >
          Paper
        </button>
        <button
          type="button"
          className={`rounded border px-2 py-1 text-xs ${
            gexEnabled ? "border-amber-400/60 bg-amber-400/10 text-amber-200" : "border-white/15 text-white/80"
          } disabled:opacity-40`}
          disabled={symbol !== "NIFTY50"}
          title={
            symbol === "NIFTY50"
              ? "Gamma exposure from the live Nifty option chain (nearest weekly expiry)"
              : "GEX is only for Nifty 50"
          }
          onClick={() => setGexEnabled(!gexEnabled)}
          data-testid="gex-toggle"
        >
          GEX
        </button>
        <button
          type="button"
          className={`rounded border px-2 py-1 text-xs ${
            marketPanelOpen ? "border-sky-400/60 bg-sky-400/10 text-sky-200" : "border-white/15 text-white/80"
          }`}
          title="Pre-market view: global, FII/DII, crude, India VIX, structure"
          onClick={() => setMarketPanelOpen(!marketPanelOpen)}
          data-testid="market-view-toggle"
        >
          View
        </button>
        {status === "loading" && <span className="text-xs text-white/40">loading…</span>}
          </>
        ) : mainView === "options" ? (
          <span className="text-xs text-white/45">Nifty option chain</span>
        ) : mainView === "sensex" ? (
          <span className="text-xs text-white/45">Sensex chart</span>
        ) : mainView === "scanner" ? (
          <span className="text-xs text-white/45">Stock scanner</span>
        ) : (
          <span className="text-xs text-white/45">Paper forward-test desk</span>
        )}
        <div className="ml-auto flex items-center gap-2">
          <LiveBadge />
          <DataTokenBadge />
          <SnapshotBadge />
          <div className="flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3 py-1">
            <span className={`h-2 w-2 rounded-full ${ui.dot}`} />
            <span className={`text-xs font-medium ${ui.text}`}>{ui.label}</span>
          </div>
        </div>
      </header>

      {/*
        Chart stays mounted when Options is active (CSS-hide only). Unmounting
        destroyed the LWC visible range and remounted into a past window.
      */}
      <div className="relative min-h-0 flex-1">
      <div
        className={`absolute inset-0 flex ${chartHidden(mainView) ? "invisible pointer-events-none" : ""}`}
        aria-hidden={chartHidden(mainView)}
        data-testid="chart-pane"
      >
      <ChartScopeProvider scope={NIFTY_SCOPE}>
      <div className="flex min-h-0 flex-1">
      {pineOpen && (
        <div className="h-full shrink-0">
          <PinePanel />
        </div>
      )}
      {panelOpen && <BacktestPanel />}
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <main className="relative flex min-h-0 flex-1">
        {loaded && symbol ? (
          <>
          <DrawSync symbol={loaded.symbol} />
          <DrawToolbar />
          <div className="relative h-full min-h-0 min-w-0 flex-1">
          <ChartView
            symbol={loaded.symbol}
            displayName={info?.display_name ?? loaded.symbol}
            timeframe={loaded.timeframe}
            scope={scopeKey(loaded.symbol, loaded.timeframe, loaded.sessions)}
            candles={candles}
            loadingOlder={loadingOlder}
            loadingNewer={loadingNewer}
            onNeedOlder={() => void loadOlder()}
            onNeedNewer={() => void loadNewer()}
            markers={markers}
            focus={chartFocus}
            onPickTime={pickTime}
            tradeCard={
              <>
                <TradeCard />
                {replayCard && (
                  <div
                    data-testid="replay-trade"
                    className="absolute bottom-16 right-3 z-20 w-52 rounded border border-amber-400/40 bg-[#0b0e14]/95 p-2 text-[11px] text-white/80"
                  >
                    <p className="font-semibold text-white">
                      {replayCard.direction} trade {replayCard.id}
                    </p>
                    <p>Entry {replayCard.entry_price}</p>
                    {replayCursorTime != null && replayCard.exit_time <= replayCursorTime ? (
                      <p>Exit {replayCard.exit_price}</p>
                    ) : (
                      <p>Open</p>
                    )}
                  </div>
                )}
              </>
            }
            levels={[
              ...analysisLevels,
              ...(replayReveal?.stops.map((stop) => ({ price: stop.price, title: "stop", color: "#ef5350" })) ?? []),
              ...(gexShown ? gexLevels(gexShown) : []),
              ...marketLevels,
            ]}
            cursor={replayActive ? replayCursorTime : null}
            gex={gexShown}
            patternShade={patternShade}
          />
          {!marketPanelOpen && <MarketViewBox />}
          {gexOn && (gexData || gexError) && (
            <div
              className="pointer-events-none absolute bottom-8 left-2 z-10 rounded bg-black/55 px-2 py-0.5 font-mono text-[11px] text-white/75"
              data-testid="gex-badge"
            >
              {gexError ? (
                <span className="text-amber-300">GEX: {gexError}</span>
              ) : gexData ? (
                <span className={gexData.regime === "negative" ? "text-red-300" : "text-emerald-300"}>{gexSummary(gexData)}</span>
              ) : null}
            </div>
          )}
          </div>
          </>
        ) : (
          <div className="flex h-full flex-1 items-center justify-center text-sm text-white/40">
            {error ??
              (backendStatus === "disconnected"
                ? "Backend not reachable. Start it with: npm run dev"
                : "Loading chart…")}
          </div>
        )}
        {error && loaded && (
          <div className="absolute bottom-3 left-3 z-20 rounded bg-red-900/80 px-3 py-1.5 text-xs text-red-100">
            {error}
          </div>
        )}
        {marketPanelOpen && <MarketViewPanel />}
        {similarOpen && (
          <SimilarPanel
            symbol={symbol}
            asOf={replayActive ? replayCursorTime : null}
            onOpenMatch={openSimilarMatch}
            onReveal={revealNext}
            onClose={() => {
              setSimilarOpen(false);
              setPatternShade(null);
            }}
          />
        )}
        {paperOpen && (
          <PaperPanel
            onOpenDashboard={() => {
              setPaperOpen(false);
              setView("paper");
            }}
          />
        )}
      </main>
      <BacktestResults />
      <PaperTrades />
      </div>
      <AnalysisPanel
        symbol={symbol}
        sessions={sessions}
        requestToken={requestToken}
        onLevels={onAnalysisLevels}
        open={analysisOpen}
      />
      {pineAlertsOpen && <PineAlertsPanel />}
      </div>
      </ChartScopeProvider>
      </div>
      {mainView === "options" && (
        <div className="absolute inset-0" data-testid="options-pane">
          <OptionsTab />
        </div>
      )}
      {mainView === "scanner" && (
        <div className="absolute inset-0 bg-[#070a0f]" data-testid="scanner-pane-wrap">
          <SectionBoundary name="Stock scanner">
            <ScannerPanel />
          </SectionBoundary>
        </div>
      )}
      {mainView === "paper" && (
        <div className="absolute inset-0 bg-[#070a0f]" data-testid="paper-pane">
          <SectionBoundary name="Paper dashboard">
            <PaperDashboard />
          </SectionBoundary>
        </div>
      )}
      {/* Sensex stays mounted (CSS-hide) so its live subscription / store keep updating. */}
      <div
        className={`absolute inset-0 ${mainView === "sensex" ? "" : "invisible pointer-events-none"}`}
        aria-hidden={mainView !== "sensex"}
      >
        <SensexPanel active={mainView === "sensex"} backendReady={backendStatus === "connected"} />
      </div>
      </div>
    </div>
  );
}
