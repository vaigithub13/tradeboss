import { useEffect, useState } from "react";

import { fetchPaperWeek, type ExpiryBookView, type ExpiryLegView, type PaperStatus, type PaperWeek } from "../api/paper";
import { formatRupees } from "./present";

export const EXPIRY_STRADDLE_NOTE = "Research: build PF 1.60, validate PF 2.20, forward PF 2.80";

const STATUS_CLASS: Record<string, string> = {
  waiting: "bg-sky-500/20 text-sky-200",
  open: "bg-amber-500/25 text-amber-200",
  done: "bg-emerald-500/20 text-emerald-200",
  no_trade: "bg-white/10 text-white/55",
  missed_entry: "bg-yellow-500/20 text-yellow-200",
  off: "bg-white/5 text-white/40",
};

function clock(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms)) return "—";
  const total = Math.floor(ms / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return [h, m, s].map((n) => String(n).padStart(2, "0")).join(":");
}

function legTime(leg: ExpiryLegView): string {
  if (leg.exit_ms == null) return clock(leg.entry_ms);
  return `${clock(leg.entry_ms)} → ${clock(leg.exit_ms)}`;
}

/** Slot 9 on the paper side panel. Slots 1–8 keep their own cards. */
export function ExpiryStraddleCard({ status }: { status: PaperStatus | null }) {
  const book: ExpiryBookView | null = status?.book ?? null;
  const day = status?.day ?? null;
  const label = book?.status ?? "off";
  const [week, setWeek] = useState<PaperWeek | null>(null);

  useEffect(() => {
    if (!day) {
      setWeek(null);
      return;
    }
    let live = true;
    void fetchPaperWeek("9", day)
      .then((row) => {
        if (live) setWeek(row);
      })
      .catch(() => {
        if (live) setWeek(null);
      });
    return () => {
      live = false;
    };
  }, [day]);

  return (
    <article
      className="min-w-0 rounded border border-white/10 bg-white/[0.03] p-2"
      data-testid="expiry-straddle-card"
      aria-label="Expiry Straddle 0920"
    >
      <div className="flex min-w-0 items-center gap-2">
        <h3 className="min-w-0 flex-1 truncate text-[12px] font-semibold text-white">9 · Expiry Straddle 0920</h3>
        <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] uppercase tracking-wide ${STATUS_CLASS[label] ?? STATUS_CLASS.off}`}>
          {label}
        </span>
      </div>
      <p className="mt-1 text-[10px] leading-snug text-white/45" data-testid="expiry-straddle-note">
        {EXPIRY_STRADDLE_NOTE}
      </p>
      <div className="mt-1.5 flex justify-between gap-2 tabular-nums text-[11px] text-white/70">
        <span>Spot {book?.spot_at_entry ?? "—"}</span>
        <span>Strike {book?.strike ?? "—"}</span>
      </div>
      {label === "no_trade" && (
        <p className="mt-1 text-[11px] text-white/50">no trade today (not expiry)</p>
      )}
      <div className="mt-1.5 space-y-1">
        {(book?.legs ?? []).map((leg) => (
          <div key={leg.kind} className="rounded bg-black/30 px-1.5 py-1 text-[11px] text-white/75" data-testid={`expiry-leg-${leg.kind}`}>
            <div className="font-medium text-white">{leg.kind}</div>
            <div className="flex justify-between gap-2 tabular-nums">
              <span>entry {leg.entry}</span>
              <span>stop {leg.stop}</span>
            </div>
            <div className="flex justify-between gap-2 tabular-nums text-white/55">
              <span>exit {leg.exit ?? "—"}</span>
              <span>{leg.reason ?? "—"}</span>
            </div>
            <div className="text-white/45">{legTime(leg)}</div>
          </div>
        ))}
      </div>
      <div className="mt-1.5 space-y-0.5 text-[11px] tabular-nums" data-testid="expiry-straddle-pnl">
        <div className="flex justify-between">
          <span className="text-white/45">Net</span>
          <span className={book && book.net < 0 ? "text-red-400" : "text-emerald-400"}>{formatRupees(book?.net)}</span>
        </div>
        <div className="flex justify-between text-white/70">
          <span>Gross</span>
          <span>{formatRupees(book?.gross)}</span>
        </div>
        <div className="flex justify-between text-white/70">
          <span>Charges</span>
          <span>{formatRupees(book?.costs)}</span>
        </div>
      </div>
      <div className="mt-1.5 border-t border-white/10 pt-1.5 text-[11px] tabular-nums" data-testid="expiry-straddle-week">
        <div className="text-[10px] uppercase tracking-wide text-white/40">{week ? `Week ${week.week}` : "Week"}</div>
        <div className="flex justify-between">
          <span className="text-white/45">Net</span>
          <span className={(week?.totals.net ?? 0) < 0 ? "text-red-400" : "text-emerald-400"}>{formatRupees(week?.totals.net)}</span>
        </div>
        <div className="flex justify-between text-white/70">
          <span>Gross</span>
          <span>{formatRupees(week?.totals.gross)}</span>
        </div>
        <div className="flex justify-between text-white/70">
          <span>Charges</span>
          <span>{formatRupees(week?.totals.costs)}</span>
        </div>
      </div>
    </article>
  );
}
