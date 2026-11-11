import type { AccountStopProgress } from "./real";
import { formatRupees } from "./present";

/** Progress toward the account loss stop (default ₹5,000). Warns clearly when reached. */
export function AccountStopBar({
  stop,
  onChangeStop,
}: {
  stop: AccountStopProgress;
  onChangeStop?: (value: number) => void;
}) {
  const pct = Math.round(stop.fraction * 100);
  return (
    <div className="flex flex-col gap-1 rounded border border-white/10 p-2" aria-label="Account stop">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold text-white">Account stop</span>
        <span className="text-white/50">
          loss used {formatRupees(stop.loss_used)} / {formatRupees(stop.account_stop)} · real net{" "}
          {formatRupees(stop.real_net)}
        </span>
        {onChangeStop && (
          <label className="ml-auto flex items-center gap-1 text-white/50">
            Limit ₹
            <input
              type="number"
              min={1}
              step={100}
              className="w-24 rounded border border-white/15 bg-transparent px-1 py-0.5 tabular-nums"
              value={stop.account_stop}
              aria-label="Account stop limit"
              onChange={(e) => {
                const n = Number(e.target.value);
                if (Number.isFinite(n) && n > 0) onChangeStop(n);
              }}
            />
          </label>
        )}
      </div>
      <div className="h-2 overflow-hidden rounded bg-white/10" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
        <div
          className={`h-full ${stop.reached ? "bg-red-500" : pct >= 70 ? "bg-amber-400" : "bg-emerald-500"}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      {stop.reached && (
        <div className="rounded bg-red-500/20 px-2 py-1 font-semibold text-red-300" role="alert">
          Account stop reached — real loss is at or beyond ₹{stop.account_stop.toFixed(0)}. Stop manual trading.
        </div>
      )}
    </div>
  );
}
