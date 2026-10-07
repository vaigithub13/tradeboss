import { useCallback, useEffect, useRef, useState } from "react";

import type { SeriesMarker, Time, UTCTimestamp } from "lightweight-charts";

import { ChartView } from "./chart/ChartView";
import { DrawSync, DrawToolbar } from "./draw/toolbar";
import { TimeframeBar } from "./chart/TimeframeBar";
import { AnalysisPanel } from "./panels/AnalysisPanel";
import { BacktestPanel } from "./panels/BacktestPanel";
import { PinePanel } from "./panels/PinePanel";
import { BacktestResults, TradeCard } from "./panels/BacktestResults";
import { IndicatorsMenu } from "./panels/IndicatorsMenu";
import { DataTokenBadge } from "./panels/DataTokenBadge";
import { LiveBadge } from "./panels/LiveBadge";
import { SnapshotBadge } from "./panels/SnapshotBadge";
import { PaperPanel } from "./panels/PaperPanel";
import { paperMarkers, type PaperSignal } from "./paper/present";
import { usePaperStore } from "./store/paperStore";
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

const POLL_INTERVAL_MS = 3000;
const NO_PAPER_SIGNALS: PaperSignal[] = [];
/** wait for typing in a parameter box to settle before asking the backend */
const INDICATOR_DEBOUNCE_MS = 200;

const STATUS_UI: Record<BackendStatus, { label: string; dot: string; text: string }> = {
  checking: { label: "checking backend…", dot: "bg-yellow-400", text: "text-yellow-300" },
  connected: { label: "backend connected", dot: "bg-emerald-400", text: "text-emerald-300" },
  disconnected: { label: "backend disconnected", dot: "bg-red-500", text: "text-red-400" },
};

export default function App() {
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
    const controller = new AbortController();
    void poll(controller.signal);
    const id = window.setInterval(() => void poll(controller.signal), POLL_INTERVAL_MS);
    return () => {
      controller.abort();
      window.clearInterval(id);
    };
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
  const setLiveView = useLiveStore((s) => s.setView);
  useEffect(() => {
    startLive();
    return () => stopLive();
  }, [startLive, stopLive]);

  // Tell the server what is on screen (symbol, timeframe, sessions, visible indicators).
  const loadedSymbol = loaded?.symbol;
  const loadedTimeframe = loaded?.timeframe;
  const loadedSessions = loaded?.sessions;
  useEffect(() => {
    const view = liveView(
      loadedSymbol && loadedTimeframe && loadedSessions
        ? { symbol: loadedSymbol, timeframe: loadedTimeframe, sessions: loadedSessions }
        : null,
      indicatorItems,
    );
    setLiveView(view);
  }, [loadedSymbol, loadedTimeframe, loadedSessions, indicatorItems, setLiveView]);

  const info = symbols.find((s) => s.symbol === symbol);
  const ui = STATUS_UI[backendStatus];
  const panelOpen = useBacktestStore((s) => s.panelOpen);
  const replayActive = useReplayStore((s) => s.active);
  const replayCursorTime = useReplayStore((s) => s.cursor);
  useReplayClock();
  const setPanelOpen = useBacktestStore((s) => s.setPanelOpen);
  const [pineOpen, setPineOpen] = useState(false);
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
  if (symbol === "NIFTY50" && !replayActive && paperOpen) {
    const times = candles.map((c) => c.time);
    for (const slot of paperStatus?.slots ?? []) {
      markers.push(...paperMarkers(slot.signals ?? NO_PAPER_SIGNALS, times, slot.slot === "1" ? "" : `${slot.slot}·`));
    }
  }
  markers.sort((a, b) => Number(a.time) - Number(b.time));
  const pickTime = (time: number): void => {
    const trade = activeRun?.result?.trades.find((item) => item.entry_time === time || item.exit_time === time);
    if (!trade) return;
    void showAround(trade.entry_time).then(() => selectTrade(trade.id, trade.entry_time));
  };

  return (
    <div className="flex h-full flex-col">
      <header className="flex min-h-11 shrink-0 flex-wrap items-center gap-4 border-b border-white/10 px-3 py-1">
        <span className="text-sm font-semibold tracking-tight">Chart Analyser</span>
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
          onClick={() => {
            setAnalysisOpen(true);
            setRequestToken((token) => token + 1);
          }}
        >
          Analyse
        </button>
        <button
          type="button"
          className="rounded border border-white/15 px-2 py-1 text-xs text-white/80"
          onClick={() => setPaperOpen((open) => !open)}
        >
          Paper
        </button>
        {status === "loading" && <span className="text-xs text-white/40">loading…</span>}
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
            focus={focus}
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
            ]}
            cursor={replayActive ? replayCursorTime : null}
          />
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
        {paperOpen && <PaperPanel />}
      </main>
      <BacktestResults />
      </div>
      <AnalysisPanel
        symbol={symbol}
        sessions={sessions}
        requestToken={requestToken}
        onLevels={onAnalysisLevels}
        open={analysisOpen}
      />
      </div>
    </div>
  );
}
