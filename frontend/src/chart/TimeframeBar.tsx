import { TIMEFRAMES, type SymbolInfo, type Timeframe } from "../api/client";
import { timeframeState } from "../store/chartStore";

interface Props {
  info: SymbolInfo | undefined;
  selected: Timeframe;
  onSelect: (tf: Timeframe) => void;
}

export function TimeframeBar({ info, selected, onSelect }: Props) {
  return (
    <div className="flex items-center gap-1" role="group" aria-label="Timeframe">
      {TIMEFRAMES.map((tf) => {
        const state = timeframeState(info, tf);
        const active = tf === selected;
        return (
          <button
            key={tf}
            type="button"
            disabled={!state.enabled}
            title={state.reason ?? undefined}
            aria-pressed={active}
            onClick={() => onSelect(tf)}
            className={[
              "rounded px-2.5 py-1 text-xs font-medium transition-colors",
              active ? "bg-sky-600 text-white" : "text-white/70 hover:bg-white/10",
              !state.enabled ? "cursor-not-allowed opacity-30 hover:bg-transparent" : "",
            ].join(" ")}
          >
            {tf}
          </button>
        );
      })}
    </div>
  );
}
