import type { PaperStatus } from "../api/paper";
import { formatRupees } from "./present";
import { livePositionView } from "./dashboard";
import { STATUS_PILL_CLASS, slotLabel, slotStatusPill } from "./slots";

/** One compact live card for the Paper side panel. */
export function CompactLiveCard({ status }: { status: PaperStatus | null; slot: string }) {
  const slot = status?.slot ?? "";
  const pill = slotStatusPill(status);
  const net = status?.summary?.net ?? null;
  const trades = status?.summary?.trades ?? 0;
  const live = livePositionView(status);

  return (
    <article
      className="min-w-0 rounded border border-white/10 bg-white/[0.03] p-2"
      data-testid={`paper-live-card-${slot}`}
      aria-label={`Slot ${slot}`}
    >
      <div className="flex min-w-0 items-center gap-2">
        <h3 className="min-w-0 flex-1 truncate text-[12px] font-semibold text-white" title={slotLabel(slot)}>
          {slot} · {slotLabel(slot)}
        </h3>
        <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] uppercase tracking-wide ${STATUS_PILL_CLASS[pill]}`}>
          {pill}
        </span>
      </div>
      <div className="mt-1 flex items-baseline justify-between gap-2 tabular-nums">
        <span className={`text-sm font-medium ${net != null && net < 0 ? "text-red-400" : "text-emerald-400"}`}>
          {formatRupees(net)}
        </span>
        <span className="text-white/45">{trades} trades</span>
      </div>
      {live && (
        <div className="mt-1.5 space-y-0.5 rounded bg-black/30 px-1.5 py-1 text-[11px] text-white/75" data-testid="live-position">
          <div className="flex justify-between gap-2">
            <span className="truncate" title={live.contract}>
              {live.side} {live.strike}
            </span>
            <span className={live.unrealised != null && live.unrealised < 0 ? "text-red-400" : "text-emerald-400"}>
              {formatRupees(live.unrealised)}
            </span>
          </div>
          <div className="flex justify-between text-white/50">
            <span>Entry {live.entry != null ? live.entry.toFixed(2) : "—"}</span>
            <span title="Live bid premium is not exposed by /api/paper/status">LTP —</span>
          </div>
          <div className="flex justify-between text-white/50">
            <span>SL {live.stop != null ? live.stop.toFixed(2) : "—"}</span>
            <span>TG {live.target != null ? live.target.toFixed(2) : "—"}</span>
          </div>
        </div>
      )}
    </article>
  );
}
