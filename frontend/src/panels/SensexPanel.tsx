/**
 * The SENSEX tab: its own chart (timeframes, indicators, drawings, replay, candle countdown), the
 * Sensex market view (CPR / pivots / ATM straddle) and the AI analysis.
 *
 * Everything runs on the Sensex stores through `ChartScopeProvider`, so nothing here touches the
 * Nifty chart's symbol, timeframe, candles, indicators or replay. No GEX, paper or backtest overlays.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import type { LevelLine } from "../ai/present";
import { TimeframeBar } from "../chart/TimeframeBar";
import { ChartView } from "../chart/ChartView";
import { DrawSync, DrawToolbar } from "../draw/toolbar";
import { scopeKey } from "../indicators/cache";
import { viewLevels } from "../marketView/present";
import { ReplayBar, useReplayClock } from "../replay/ReplayBar";
import { useSensexReplayStore } from "../replay/store";
import { sensexReplaySession } from "../replay/session";
import { SENSEX_SCOPE, ChartScopeProvider } from "../store/chartScope";
import { useSensexChartStore } from "../store/chartStore";
import { useChartsConfigStore } from "../store/chartsConfigStore";
import { useSensexIndicatorStore } from "../store/indicatorStore";
import { SectionBoundary } from "../paper/SectionBoundary";
import { AnalysisPanel } from "./AnalysisPanel";
import { IndicatorsMenu } from "./IndicatorsMenu";
import { SensexMarketView, useSensexMarketView } from "./SensexMarketView";
import { SessionsMenu } from "./SessionsMenu";

/** wait for typing in a parameter box to settle before asking the backend */
const INDICATOR_DEBOUNCE_MS = 200;
const LEVELS_KEY = "tradeboss.sensex.levels";

const getSensexCursor = (): number | null => sensexReplaySession.get();

function readLevelsPref(): boolean {
  try {
    return globalThis.localStorage?.getItem(LEVELS_KEY) !== "0";
  } catch {
    return true;
  }
}

interface Props {
  /** the Sensex tab is the one on screen (the pane stays mounted, CSS-hidden, otherwise) */
  active: boolean;
  /** the backend answered its health check; chart data is only requested after that */
  backendReady: boolean;
}

export function SensexPanel({ active, backendReady }: Props) {
  const symbols = useSensexChartStore((s) => s.symbols);
  const symbol = useSensexChartStore((s) => s.symbol);
  const sessions = useSensexChartStore((s) => s.sessions);
  const timeframe = useSensexChartStore((s) => s.timeframe);
  const candles = useSensexChartStore((s) => s.candles);
  const loaded = useSensexChartStore((s) => s.loaded);
  const loadingOlder = useSensexChartStore((s) => s.loadingOlder);
  const loadingNewer = useSensexChartStore((s) => s.loadingNewer);
  const loadOlder = useSensexChartStore((s) => s.loadOlder);
  const loadNewer = useSensexChartStore((s) => s.loadNewer);
  const status = useSensexChartStore((s) => s.status);
  const error = useSensexChartStore((s) => s.error);
  const init = useSensexChartStore((s) => s.init);
  const setTimeframe = useSensexChartStore((s) => s.setTimeframe);
  const indicatorItems = useSensexIndicatorStore((s) => s.items);
  const refreshIndicators = useSensexIndicatorStore((s) => s.refresh);
  const loadConfig = useChartsConfigStore((s) => s.load);
  const replayActive = useSensexReplayStore((s) => s.active);
  const replayCursorTime = useSensexReplayStore((s) => s.cursor);
  useReplayClock(useSensexReplayStore);

  // Open the chart on the symbol the backend names (once the backend is reachable).
  const started = useRef(false);
  useEffect(() => {
    if (!backendReady) {
      started.current = false; // try again when the backend comes back
      return;
    }
    if (started.current || symbols.length > 0 || status === "loading") return;
    started.current = true;
    void loadConfig().then((config) => init(config.sensex_symbol));
  }, [backendReady, symbols.length, status, loadConfig, init]);

  // Fetch whatever indicator values the loaded candles still lack (same rules as the Nifty chart).
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

  const marketView = useSensexMarketView(backendReady);
  const [showLevels, setShowLevelsState] = useState(readLevelsPref);
  const setShowLevels = useCallback((on: boolean) => {
    setShowLevelsState(on);
    try {
      globalThis.localStorage?.setItem(LEVELS_KEY, on ? "1" : "0");
    } catch {
      // storage unavailable: the choice lasts for this tab only
    }
  }, []);

  const [analysisOpen, setAnalysisOpen] = useState(false);
  const [requestToken, setRequestToken] = useState(0);
  const [analysisLevels, setAnalysisLevels] = useState<LevelLine[]>([]);
  const onAnalysisLevels = useCallback((lines: LevelLine[]) => setAnalysisLevels(lines), []);
  useEffect(() => {
    setAnalysisLevels([]);
  }, [symbol]);

  const info = symbols.find((s) => s.symbol === symbol);
  const marketLevels = marketView.view && showLevels && !replayActive ? viewLevels(marketView.view.levels) : [];

  return (
    <SectionBoundary name="Sensex chart">
    <ChartScopeProvider scope={SENSEX_SCOPE}>
      <div className="flex h-full min-h-0 flex-col" data-testid="sensex-pane">
        <div className="flex min-h-10 shrink-0 flex-wrap items-center gap-3 border-b border-white/10 px-3 py-1">
          <span className="text-sm font-semibold text-white/90" data-testid="sensex-title">
            {info?.display_name ?? symbol ?? "SENSEX"}
          </span>
          <TimeframeBar info={info} selected={timeframe} onSelect={(tf) => void setTimeframe(tf)} />
          <SessionsMenu />
          <IndicatorsMenu />
          <ReplayBar store={useSensexReplayStore} />
          <button
            type="button"
            className="rounded border border-white/15 px-2 py-1 text-xs text-white/80"
            onClick={() => {
              setAnalysisOpen(true);
              setRequestToken((token) => token + 1);
            }}
            data-testid="sensex-analyse"
          >
            Analyse
          </button>
          {status === "loading" && <span className="text-xs text-white/40">loading…</span>}
        </div>

        <div className="flex min-h-0 flex-1">
          <main className="relative flex min-h-0 min-w-0 flex-1">
            {loaded && symbol ? (
              <>
                <DrawSync symbol={loaded.symbol} active={active} />
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
                    levels={[...analysisLevels, ...marketLevels]}
                    cursor={replayActive ? replayCursorTime : null}
                  />
                </div>
              </>
            ) : (
              <div className="flex h-full flex-1 items-center justify-center text-sm text-white/40" data-testid="sensex-empty">
                {error ?? (backendReady ? "Loading Sensex chart…" : "Backend not reachable. Start it with: npm run dev")}
              </div>
            )}
            {error && loaded && (
              <div className="absolute bottom-3 left-3 z-20 rounded bg-red-900/80 px-3 py-1.5 text-xs text-red-100">
                {error}
              </div>
            )}
          </main>
          <SensexMarketView state={marketView} showLevels={showLevels} onShowLevels={setShowLevels} />
          <AnalysisPanel
            symbol={symbol}
            sessions={sessions}
            requestToken={requestToken}
            onLevels={onAnalysisLevels}
            open={analysisOpen}
            getCursor={getSensexCursor}
            autoEnabled={false}
          />
        </div>
      </div>
    </ChartScopeProvider>
    </SectionBoundary>
  );
}
