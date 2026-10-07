import { Fragment, useEffect, useState } from "react";

import { PAPER_SLOTS, type PaperStatus } from "../api/paper";
import { formatRupees, markLine, paperRows, parseParams } from "../paper/present";
import { usePaperStore } from "../store/paperStore";

const STRATEGIES = [
  { name: "log_xz", label: "Log XZ (RMA 14, 5m)" },
  { name: "price_channel", label: "Price Channel (length 20, 5m)" },
];
/** slot 1 forward-tests the original; slot 2 the Price Channel the 7 Oct walk-forward chose */
const DEFAULT_STRATEGY: Record<string, string> = { "1": "log_xz", "2": "price_channel" };
const POLL_MS = 3000;

/** Live signals (paper): two strategies side by side on the live feed, each with its own position and P&L. No orders. */
export function PaperPanel() {
  const status = usePaperStore((s) => s.status);
  const pollError = usePaperStore((s) => s.errors[""] ?? null);
  const refresh = usePaperStore((s) => s.refresh);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => void refresh(), POLL_MS);
    return () => window.clearInterval(id);
  }, [refresh]);

  const disabled = status?.state === "disabled";

  return (
    <aside className="flex h-full w-96 shrink-0 flex-col gap-3 overflow-y-auto border-l border-white/10 bg-[#0b0f14] p-3 text-xs text-white/80">
      <div>
        <h2 className="text-sm font-semibold text-white">Live signals (paper)</h2>
        <p className="text-white/50">No orders are sent. Fills are paper: live quotes, or the model flagged as modelled.</p>
      </div>
      {pollError && <div className="text-red-400">{pollError}</div>}
      {disabled && <div className="text-yellow-300">The live feed is off, so paper trading cannot run.</div>}
      {PAPER_SLOTS.map((slot) => (
        <SlotSection key={slot} slot={slot} status={status?.slots.find((x) => x.slot === slot) ?? null} disabled={disabled} />
      ))}
    </aside>
  );
}

function SlotSection({ slot, status, disabled }: { slot: string; status: PaperStatus | null; disabled: boolean }) {
  const error = usePaperStore((s) => s.errors[slot] ?? null);
  const start = usePaperStore((s) => s.start);
  const stop = usePaperStore((s) => s.stop);
  const [strategy, setStrategy] = useState(DEFAULT_STRATEGY[slot] ?? "log_xz");
  const [paramsText, setParamsText] = useState("");
  const running = status?.state === "running";
  const summary = status?.summary ?? null;
  const parsed = parseParams(paramsText);
  const shownParams = status?.params && Object.keys(status.params).length ? JSON.stringify(status.params) : "defaults";

  return (
    <section className="flex flex-col gap-2 rounded border border-white/10 p-2" aria-label={`Paper strategy ${slot}`}>
      <h3 className="font-semibold text-white">Strategy {slot}</h3>
      <div className="flex items-center gap-2">
        <select
          aria-label={`Strategy ${slot}`}
          className="rounded border border-white/15 bg-transparent px-2 py-1"
          value={running && status?.strategy ? status.strategy : strategy}
          disabled={running}
          onChange={(e) => setStrategy(e.target.value)}
        >
          {STRATEGIES.map((s) => (
            <option key={s.name} value={s.name} className="bg-[#0b0f14]">
              {s.label}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="rounded border border-white/15 px-3 py-1 disabled:opacity-40"
          disabled={disabled || (!running && parsed.params === undefined)}
          onClick={() => void (running ? stop(slot) : start(slot, strategy, parsed.params ?? {}))}
        >
          {running ? "Stop" : "Start"}
        </button>
      </div>
      {running ? (
        <div className="text-white/50">params: {shownParams}</div>
      ) : (
        <input
          aria-label={`Params ${slot}`}
          placeholder='params, e.g. {"z_length": 20} (blank = defaults)'
          className="rounded border border-white/15 bg-transparent px-2 py-1 font-mono"
          value={paramsText}
          onChange={(e) => setParamsText(e.target.value)}
        />
      )}
      {parsed.error && !running && <div className="text-yellow-300">{parsed.error}</div>}
      <div className="text-white/60">
        state: {status?.state ?? "…"}
        {status?.ended_by ? ` (${status.ended_by})` : ""}
      </div>
      <div>Open P&amp;L: {markLine(status?.mark ?? null)}</div>
      <div>
        Day: net {formatRupees(summary?.net ?? null)} · trades {summary?.trades ?? 0} · wins {summary?.wins ?? 0} ·
        modelled legs {summary?.modelled_legs ?? 0}
      </div>
      {error && <div className="text-red-400">{error}</div>}

      <table className="w-full table-fixed text-left tabular-nums">
        <colgroup>
          <col className="w-12" />
          <col className="w-11" />
          <col className="w-[4.75rem]" />
          <col className="w-20" />
          <col />
        </colgroup>
        <thead className="text-white/40">
          <tr>
            <th className="py-1 pr-2 font-normal">Time</th>
            <th className="pr-2 font-normal">Side</th>
            <th className="pr-2 text-right font-normal">Index</th>
            <th className="pr-2 font-normal">Contract</th>
            <th className="text-right font-normal">Fill</th>
          </tr>
        </thead>
        <tbody>
          {paperRows(status?.signals ?? []).map((r, i) => (
            <Fragment key={i}>
              <tr className="border-t border-white/5 align-top">
                <td className="pt-1 pr-2">{r.time}</td>
                <td className="pt-1 pr-2">{r.side}</td>
                <td className="pt-1 pr-2 text-right">{r.index}</td>
                <td className="truncate pt-1 pr-2" title={r.contract}>{r.contract}</td>
                <td className="whitespace-nowrap pt-1 text-right">{r.fill}</td>
              </tr>
              <tr>
                <td colSpan={5} className="pb-1 text-white/50">{r.note}</td>
              </tr>
            </Fragment>
          ))}
        </tbody>
      </table>
    </section>
  );
}
