import { useEffect, useRef, useState } from "react";

import { ApiError, type SessionType } from "../api/client";
import { analyseSymbol, fetchAiSettings, fetchTrack, type AnalyseResult, type TrackResult } from "../api/ai";
import { autoDue, autoLabel, type AutoSettings } from "../ai/auto";
import { ANALYSIS_LABEL, HORIZONS, hitRateText, levelLines, type LevelLine } from "../ai/present";

interface Props {
  symbol: string | null;
  sessions: readonly SessionType[];
  /** Increment to run an analysis. Zero does not run. */
  requestToken: number;
  onLevels: (lines: LevelLine[]) => void;
  /** The panel stays mounted while this is false so the auto timer can run. */
  open?: boolean;
}

export function AnalysisPanel({ symbol, sessions, requestToken, onLevels, open = true }: Props) {
  const [track, setTrack] = useState<TrackResult | null>(null);
  const [result, setResult] = useState<AnalyseResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [settings, setSettings] = useState<AutoSettings | null>(null);
  const [autoToken, setAutoToken] = useState(0);
  const runningRef = useRef(false);
  runningRef.current = running;

  useEffect(() => {
    if (!symbol) return;
    const controller = new AbortController();
    void fetchTrack(symbol, sessions)
      .then((next) => {
        if (!controller.signal.aborted) setTrack(next);
      })
      .catch(() => {
        if (!controller.signal.aborted) setTrack(null);
      });
    return () => controller.abort();
  }, [symbol, sessions, result]);

  useEffect(() => {
    let stop = false;
    void fetchAiSettings()
      .then((next) => {
        if (!stop) setSettings(next);
      })
      .catch(() => {
        if (!stop) setSettings({ language: "en", auto: false, auto_minutes: 15 });
      });
    return () => {
      stop = true;
    };
  }, []);

  useEffect(() => {
    let last = Date.now();
    const id = window.setInterval(() => {
      const current = settings;
      if (!current || runningRef.current) return;
      if (!autoDue(new Date(), last, current.auto_minutes, current.auto)) return;
      last = Date.now();
      setAutoToken((token) => token + 1);
    }, 15_000);
    return () => window.clearInterval(id);
  }, [settings, symbol]);

  useEffect(() => {
    if ((requestToken === 0 && autoToken === 0) || !symbol) return;
    const controller = new AbortController();
    setRunning(true);
    setError(null);
    void analyseSymbol(symbol, sessions)
      .then((next) => {
        if (controller.signal.aborted) return;
        setResult(next);
        onLevels(next.analysis ? levelLines(next.analysis) : []);
        if (!next.ready) setError(next.error ?? "The analysis was not ready.");
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setError(err instanceof ApiError ? err.message : "The analysis request failed.");
      })
      .finally(() => {
        if (!controller.signal.aborted) setRunning(false);
      });
    return () => controller.abort();
  }, [requestToken, autoToken, symbol, sessions, onLevels]);

  const analysis = result?.analysis;

  return (
    <aside className={`${open ? "flex" : "hidden"} h-full w-80 shrink-0 flex-col overflow-y-auto border-l border-white/10 bg-[#0e1219] text-xs text-white/80`}>
      <div className="border-b border-white/10 px-3 py-2">
        <p className="font-medium text-white">{ANALYSIS_LABEL}</p>
        <p className="mt-1 text-white/40">{symbol ?? "No symbol"}</p>
      </div>
      <div className="space-y-3 px-3 py-3">
        {running && <p className="text-white/50">Analysing…</p>}
        {error && <p className="text-red-300">{error}</p>}
        {analysis && (
          <section className="space-y-2">
            <p>
              Bias <span className="text-white">{analysis.bias}</span>
              <span className="text-white/40"> · confidence {analysis.confidence}</span>
              {result?.mode === "replay" && <span className="text-amber-200"> · replay</span>}
            </p>
            <p className="text-white/70">{analysis.reasoning}</p>
            <ul className="space-y-1 text-white/60">
              <li>5m {analysis.trends["5m"]}</li>
              <li>15m {analysis.trends["15m"]}</li>
              <li>1h {analysis.trends["1h"]}</li>
              <li>1D {analysis.trends["1D"]}</li>
            </ul>
            <p>Bull: {analysis.bull.note}</p>
            <p>Bear: {analysis.bear.note}</p>
            {result?.cost && <p className="text-white/50">{result.model} · {result.cost}</p>}
          </section>
        )}
        {settings && <p className="text-white/40">{autoLabel(settings)}</p>}
        <section className="space-y-2">
          <p className="font-medium text-white/90">Track record</p>
          {HORIZONS.map((horizon) => {
            const block = track?.horizons[horizon.id];
            const lines = hitRateText({
              label: horizon.label,
              scored: block?.scored ?? 0,
              ai: block?.ai ?? { bias: null, triggers: null, levels: null },
              alwaysBullish: { bias: block?.always_bullish.bias ?? null },
              followTrend: { bias: block?.follow_trend.bias ?? null },
            });
            return (
              <div key={horizon.id} className="space-y-0.5 border-t border-white/5 pt-2">
                {lines.map((line) => (
                  <p key={line} className="text-white/60">{line}</p>
                ))}
              </div>
            );
          })}
        </section>
      </div>
    </aside>
  );
}
