import { useMarketViewStore } from "../store/marketViewStore";
import { biasColor, dayTypeLabel, factorColor, formatRange, rateText } from "../marketView/present";

function builtAt(seconds: number): string {
  return new Date(seconds * 1000).toLocaleTimeString("en-IN", {
    timeZone: "Asia/Kolkata",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

/** Full pre-market view beside the chart. */
export function MarketViewPanel() {
  const view = useMarketViewStore((s) => s.view);
  const history = useMarketViewStore((s) => s.history);
  const error = useMarketViewStore((s) => s.error);
  const loading = useMarketViewStore((s) => s.loading);
  const load = useMarketViewStore((s) => s.load);
  const setPanelOpen = useMarketViewStore((s) => s.setPanelOpen);
  const showLevels = useMarketViewStore((s) => s.showLevels);
  const setShowLevels = useMarketViewStore((s) => s.setShowLevels);

  return (
    <aside
      className="flex h-full w-72 shrink-0 flex-col gap-3 overflow-y-auto border-l border-white/10 bg-[#0b0f14] p-3 text-xs text-white/80"
      data-testid="market-view-panel"
    >
      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold text-white">
          Market view{view ? ` · ${builtAt(view.built_at)}` : ""}
        </span>
        <div className="flex gap-1">
          <button
            type="button"
            className="rounded border border-white/15 px-2 py-0.5 text-[11px] text-white/70 disabled:opacity-40"
            disabled={loading}
            onClick={() => void load(true)}
          >
            {loading ? "…" : "Refresh"}
          </button>
          <button
            type="button"
            className="rounded border border-white/15 px-2 py-0.5 text-[11px] text-white/70"
            onClick={() => setPanelOpen(false)}
            title="Collapse to a small box on the chart"
          >
            Collapse
          </button>
        </div>
      </div>

      {error && <p className="text-amber-300">Couldn't load the view: {error}</p>}
      {!view && !error && <p className="text-white/40">Loading…</p>}

      {view && (
        <>
          <ul className="flex flex-col">
            {view.factors.map((f) => (
              <li key={f.key} className="border-b border-white/10 py-1.5" title={f.reason}>
                <div className="flex items-center justify-between">
                  <span className="flex items-center gap-2">
                    <span className="inline-block h-2 w-2 rounded-full" style={{ background: factorColor(f) }} />
                    {f.label}
                  </span>
                  <span className="font-mono text-white/70">{f.available ? f.value : "unavailable"}</span>
                </div>
                <p className="pl-4 text-[11px] text-white/40">{f.reason}</p>
              </li>
            ))}
          </ul>

          <div>
            <p className="text-sm font-semibold" style={{ color: biasColor(view.score) }}>
              {view.bias} <span className="font-normal text-white/40">(score {view.score > 0 ? "+" : ""}{view.score})</span>
            </p>
            <p className="text-white/60">Expected range {formatRange(view.expected_range)}</p>
            <p className="text-white/60" title={view.day_type_reason}>
              {dayTypeLabel(view)} <span className="text-white/35">· {view.day_type_reason}</span>
            </p>
          </div>

          <p className="rounded border border-white/10 bg-white/5 p-2 text-white/80">{view.plan}</p>

          <label className="flex items-center gap-2 text-white/60">
            <input type="checkbox" checked={showLevels} onChange={(e) => setShowLevels(e.target.checked)} />
            Show levels on chart
          </label>

          {view.outcome && (
            <div className="text-white/50">
              <p className="text-white/70">After the close</p>
              <p>
                Move {view.outcome.move > 0 ? "+" : ""}
                {Math.round(view.outcome.move)} pts · {view.outcome.actual_type} day · bias{" "}
                {view.outcome.bias_hit === null ? "n/a" : view.outcome.bias_hit ? "right" : "wrong"}
              </p>
            </div>
          )}

          {history && (
            <div className="text-white/45">
              <p className="text-white/60">Track record</p>
              <p>Bias right: {rateText(history.bias_hit)}</p>
              <p>Range held: {rateText(history.range_held)}</p>
              <p>Day type right: {rateText(history.day_type_hit)}</p>
            </div>
          )}

          <p className="text-[10px] leading-snug text-white/30">
            Context, not a signal. Global and crude from Yahoo Finance, FII/DII from NSE, the rest from TradeBoss data.
          </p>
        </>
      )}
    </aside>
  );
}

/** Compact verdict on the chart once the panel is collapsed. Click to open the full panel. */
export function MarketViewBox() {
  const view = useMarketViewStore((s) => s.view);
  const setPanelOpen = useMarketViewStore((s) => s.setPanelOpen);
  if (!view) return null;
  return (
    <button
      type="button"
      onClick={() => setPanelOpen(true)}
      className="absolute right-24 top-2 z-10 flex items-center gap-2 rounded border border-white/10 bg-black/60 px-2 py-1 font-mono text-[11px] text-white/80 backdrop-blur-[2px]"
      title="Open the market view"
      data-testid="market-view-box"
    >
      <span style={{ color: biasColor(view.score) }}>{view.bias}</span>
      <span className="text-white/40">·</span>
      <span>{view.day_type === "mixed" ? "unclear" : `${view.day_type} day`}</span>
      <span className="flex gap-1">
        {view.factors.map((f) => (
          <span key={f.key} className="inline-block h-2 w-2 rounded-full" style={{ background: factorColor(f) }} />
        ))}
      </span>
    </button>
  );
}
