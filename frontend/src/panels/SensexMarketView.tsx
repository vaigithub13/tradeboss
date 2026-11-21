import { useCallback, useEffect, useRef, useState } from "react";

import { fetchSensexMarketView, type SensexMarketView as SensexView } from "../api/chartsConfig";
import { biasColor, dayTypeLabel, formatRange } from "../marketView/present";

/** the view is saved per day; re-read every 5 minutes so an open tab picks up a rebuild */
const POLL_MS = 5 * 60_000;

export interface SensexViewState {
  view: SensexView | null;
  error: string | null;
  loading: boolean;
  refresh: () => void;
}

/** Loads GET /api/market-view/sensex when `enabled`, then every few minutes. */
export function useSensexMarketView(enabled: boolean): SensexViewState {
  const [view, setView] = useState<SensexView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [token, setToken] = useState(0);
  const forceRef = useRef(false);

  useEffect(() => {
    if (!enabled) return;
    const ac = new AbortController();
    const run = (refresh: boolean): void => {
      setLoading(true);
      fetchSensexMarketView(refresh, ac.signal)
        .then((next) => {
          if (ac.signal.aborted) return;
          setView(next);
          setError(null);
        })
        .catch((err: unknown) => {
          if (ac.signal.aborted) return;
          setError(err instanceof Error ? err.message : String(err));
        })
        .finally(() => {
          if (!ac.signal.aborted) setLoading(false);
        });
    };
    run(forceRef.current);
    forceRef.current = false;
    const id = window.setInterval(() => run(false), POLL_MS);
    return () => {
      window.clearInterval(id);
      ac.abort();
    };
  }, [enabled, token]);

  const refresh = useCallback(() => {
    forceRef.current = true;
    setToken((n) => n + 1);
  }, []);

  return { view, error, loading, refresh };
}

const fmt = (n: number): string => n.toLocaleString("en-IN", { maximumFractionDigits: 2 });

/** Slim Sensex market view: bias, CPR / pivot levels and the ATM straddle's expected move. */
export function SensexMarketView({
  state,
  showLevels,
  onShowLevels,
}: {
  state: SensexViewState;
  showLevels: boolean;
  onShowLevels: (on: boolean) => void;
}) {
  const { view, error, loading, refresh } = state;
  const move = view?.expected_move ?? null;
  return (
    <aside
      className="flex h-full w-64 shrink-0 flex-col gap-3 overflow-y-auto border-l border-white/10 bg-[#0b0f14] p-3 text-xs text-white/80"
      data-testid="sensex-market-view"
    >
      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold text-white">Sensex view</span>
        <button
          type="button"
          className="rounded border border-white/15 px-2 py-0.5 text-[11px] text-white/70 disabled:opacity-40"
          disabled={loading}
          onClick={refresh}
        >
          {loading ? "…" : "Refresh"}
        </button>
      </div>

      {error && <p className="text-amber-300">Couldn't load the Sensex view: {error}</p>}
      {!view && !error && <p className="text-white/40">Loading…</p>}

      {view && (
        <>
          <div>
            <p className="text-sm font-semibold" style={{ color: biasColor(view.score) }}>
              {view.bias} <span className="font-normal text-white/40">(score {view.score > 0 ? "+" : ""}{view.score})</span>
            </p>
            <p className="text-white/60" title={view.day_type_reason}>
              {dayTypeLabel(view)}
            </p>
          </div>

          <p className="rounded border border-white/10 bg-white/5 p-2 text-white/80" data-testid="sensex-plan">{view.plan}</p>

          <div data-testid="sensex-straddle">
            <p className="mb-1 font-medium text-white/90">ATM straddle</p>
            {move ? (
              <ul className="space-y-0.5 font-mono text-white/70">
                <li>
                  Spot {fmt(move.spot)} · ATM {fmt(move.atm)}
                  {move.expiry ? ` · exp ${move.expiry}` : ""}
                </li>
                <li>
                  CE {fmt(move.call_price ?? move.call_ltp ?? 0)} ({move.call_source ?? "ltp"}) · PE{" "}
                  {fmt(move.put_price ?? move.put_ltp ?? 0)} ({move.put_source ?? "ltp"})
                </li>
                <li>Expected move ±{fmt(move.expected_move)} pts</li>
                <li>Range {formatRange(move.range)}</li>
              </ul>
            ) : (
              <p className="text-white/40" data-testid="sensex-straddle-unavailable">
                Unavailable
                {view.expected_move_reason ? ` (${view.expected_move_reason})` : " (no option chain)"}
              </p>
            )}
          </div>

          <div data-testid="sensex-levels">
            <p className="mb-1 font-medium text-white/90">CPR / pivots</p>
            {view.levels.length === 0 ? (
              <p className="text-white/40">Not enough daily candles yet.</p>
            ) : (
              <ul className="space-y-0.5 font-mono text-white/70">
                {view.levels.map((l) => (
                  <li key={l.key} className="flex justify-between">
                    <span>{l.label}</span>
                    <span>{fmt(l.price)}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <label className="flex items-center gap-2 text-white/60">
            <input type="checkbox" checked={showLevels} onChange={(e) => onShowLevels(e.target.checked)} />
            Show levels on chart
          </label>

          <p className="text-[10px] leading-snug text-white/30">
            Context, not a signal. Prior-day structure from stored Sensex candles; straddle from the BSE option chain.
          </p>
        </>
      )}
    </aside>
  );
}
