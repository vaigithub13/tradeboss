import { useEffect, useState } from "react";

import {
  INDEX_SYMBOLS,
  SIMILAR_TIMEFRAMES,
  fetchSimilarCandles,
  fetchSimilarDays,
  type CandleMatch,
  type CandleResult,
  type DayResult,
  type SimilarTimeframe,
} from "../api/similar";
import { istStamp, nextRevealCount, reliabilityLine, summaryLine, verdictClass } from "../similar/present";
import { PredictionReview } from "./PredictionReview";
import type { ReviewRow } from "../api/similar";

interface Props {
  symbol: string | null;
  /** Replay cursor. Null on the live chart, and never a time after the cursor is sent. */
  asOf: number | null;
  onOpenMatch: (match: CandleMatch, timeframe: SimilarTimeframe) => void;
  onReveal?: (target: { start: number; end: number; timeframe: SimilarTimeframe }, count: number) => Promise<"shown" | "waiting">;
  onClose: () => void;
}

export function SimilarPanel({ symbol, asOf, onOpenMatch, onReveal, onClose }: Props) {
  const [revealShown, setRevealShown] = useState<Record<string, number>>({});
  const [revealNote, setRevealNote] = useState<string | null>(null);
  const [timeframe, setTimeframe] = useState<SimilarTimeframe>("5m");
  const [window, setWindow] = useState(30);
  const [method, setMethod] = useState<"euclidean" | "dtw">("euclidean");
  const [at, setAt] = useState<"09:30" | "10:15" | "now">("10:15");
  const [candles, setCandles] = useState<CandleResult | null>(null);
  const [days, setDays] = useState<DayResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const index = symbol != null && (INDEX_SYMBOLS as readonly string[]).includes(symbol);

  useEffect(() => {
    if (!symbol) return;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    const candleReq = fetchSimilarCandles(symbol, timeframe, window, asOf, method, controller.signal);
    const dayReq = index ? fetchSimilarDays(symbol, at, asOf, controller.signal) : Promise.resolve(null);
    void Promise.all([candleReq, dayReq])
      .then(([nextCandles, nextDays]) => {
        setCandles(nextCandles);
        setDays(nextDays);
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setError(err instanceof Error ? err.message : "Couldn't load similar patterns");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [symbol, timeframe, window, asOf, at, index, method]);

  return (
    <aside
      className="flex h-full w-96 shrink-0 flex-col gap-3 overflow-y-auto border-l border-white/10 bg-[#0b0f14] p-3 text-xs text-white/80"
      data-testid="similar-panel"
    >
      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold text-white">Similar patterns</span>
        <button type="button" className="rounded border border-white/15 px-2 py-0.5 text-[11px]" onClick={onClose}>
          Close
        </button>
      </div>
      {onReveal && (
        <PredictionReview
          onReveal={(row: ReviewRow, count: number) =>
            onReveal({ start: row.query_start, end: row.query_end, timeframe: "15m" }, count)
          }
        />
      )}
      {revealNote && <p className="text-amber-200">{revealNote}</p>}
      <p className="rounded border border-white/10 bg-white/5 p-2 text-white/70" data-testid="similar-note">
        Read-only context. Not a signal. No order is sent. Intraday windows that cross the overnight gap are excluded.
      </p>
      {error && <p className="text-amber-300">{error}</p>}
      {loading && <p className="text-white/40">Looking…</p>}

      <div className="flex flex-wrap gap-1">
        {SIMILAR_TIMEFRAMES.map((tf) => (
          <button
            key={tf}
            type="button"
            className={`rounded px-2 py-0.5 ${timeframe === tf ? "bg-white/15 text-white" : "text-white/50"}`}
            onClick={() => setTimeframe(tf)}
          >
            {tf}
          </button>
        ))}
        <label className="ml-auto flex items-center gap-1 text-white/50">
          N
          <input
            className="w-12 rounded border border-white/15 bg-transparent px-1 py-0.5 text-white"
            type="number"
            min={5}
            max={200}
            value={window}
            onChange={(event) => setWindow(Number(event.target.value) || 30)}
          />
        </label>
        <button
          type="button"
          className="rounded border border-white/15 px-2 py-0.5 text-white/60"
          onClick={() => setMethod(method === "euclidean" ? "dtw" : "euclidean")}
        >
          {method === "euclidean" ? "Euclidean" : "DTW"}
        </button>
      </div>

      {candles && (
        <section className="flex flex-col gap-2">
          <p className="text-white/50">{candles.window_rule}</p>
          {(["5", "10", "20"] as const).map((horizon) => {
            const block = candles.summary[horizon];
            const reliability = candles.reliability?.[horizon];
            return (
              <div key={horizon}>
                {block && <p>{summaryLine(horizon, block)}</p>}
                <p className={verdictClass(reliability?.n ?? 0, reliability?.verdict ?? "")} data-testid={`reliability-${horizon}`}>
                  {reliabilityLine(reliability)}
                </p>
              </div>
            );
          })}
          <ul className="flex flex-col">
            {candles.matches.map((match) => (
              <li key={`${match.start}-${match.end}`} className="border-b border-white/10 py-1.5">
                <button
                  type="button"
                  className="w-full text-left hover:text-white"
                  onClick={() => onOpenMatch(match, timeframe)}
                >
                  <span className="font-mono text-white/90">{match.similarity.toFixed(0)}</span>
                  <span className="ml-2 text-white/60">
                    {istStamp(match.start)} – {istStamp(match.end)}
                  </span>
                </button>
                {onReveal && nextRevealCount(revealShown[`${match.start}-${match.end}`] ?? 0) != null && (
                  <button
                    type="button"
                    className="mt-1 rounded border border-white/15 px-1.5 py-0.5 text-[11px] text-white/70"
                    onClick={() => {
                      const count = nextRevealCount(revealShown[`${match.start}-${match.end}`] ?? 0);
                      if (count == null) return;
                      void onReveal({ start: match.start, end: match.end, timeframe }, count).then((result) => {
                        if (result === "shown") {
                          setRevealNote(null);
                          setRevealShown((prev) => ({ ...prev, [`${match.start}-${match.end}`]: count }));
                        } else setRevealNote("Those candles are not in yet.");
                      });
                    }}
                  >
                    See what happened next
                  </button>
                )}
                <p className="text-[11px] text-white/40">
                  {["5", "10", "20"].map((horizon) => {
                    const fwd = match.forward[horizon];
                    return fwd
                      ? `${horizon}: ${fwd.return_pct.toFixed(2)}% up ${fwd.mfe_atr.toFixed(2)} / down ${fwd.mae_atr.toFixed(2)} ATR`
                      : `${horizon}: —`;
                  }).join(" · ")}
                </p>
              </li>
            ))}
          </ul>
          {candles.matches.length === 0 && (
            <p className="text-white/40">{candles.empty_reason ?? "No earlier window far enough from this one."}</p>
          )}
        </section>
      )}

      <section className="flex flex-col gap-2 border-t border-white/10 pt-2">
        <p className="text-sm font-semibold text-white">Similar days</p>
        {!index && <p className="text-white/40">Similar days run on NIFTY, SENSEX, and BANKNIFTY only.</p>}
        {index && (
          <>
            <div className="flex gap-1">
              {(["09:30", "10:15", "now"] as const).map((choice) => (
                <button
                  key={choice}
                  type="button"
                  className={`rounded px-2 py-0.5 ${at === choice ? "bg-white/15 text-white" : "text-white/50"}`}
                  onClick={() => setAt(choice)}
                >
                  {choice}
                </button>
              ))}
            </div>
            {days && (
              <>
                <p className="text-white/50">{days.window_rule}</p>
                {Object.values(days.outcomes).map((outcome) => (
                  <p key={outcome.label} className={verdictClass(outcome.n, "")} data-testid={`day-${outcome.label}`}>
                    {outcome.line}
                  </p>
                ))}
                <p className={verdictClass(days.reliability?.n ?? 0, days.reliability?.verdict ?? "")}>
                  {reliabilityLine(days.reliability ?? undefined)}
                </p>
                <ul>
                  {days.matches.map((match) => (
                    <li key={match.date} className="border-b border-white/10 py-1">
                      <span className="font-mono">{match.similarity.toFixed(0)}</span> {match.date} · {match.day_type} · close{" "}
                      {match.close_vs_open_pct >= 0 ? "+" : ""}
                      {match.close_vs_open_pct.toFixed(2)}%
                      {match.broke_high ? " · through high" : ""}
                      {match.broke_low ? " · through low" : ""}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </>
        )}
      </section>
    </aside>
  );
}
