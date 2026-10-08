import { Fragment, useEffect, useState } from "react";

import { PAPER_SLOTS, fetchPaperWeek, type PaperStatus, type PaperWeek } from "../api/paper";
import { EXIT_RULES, SLOT_DEFAULTS, exitLabel, formatRupees, markLine, paperRows, parseParams, startParams } from "../paper/present";
import { exitCountsLine } from "../report/present";
import { TradeReport } from "../report/TradeReport";
import { usePaperStore } from "../store/paperStore";
import { usePaperTradesStore } from "../store/paperTradesStore";
import { useDragResize } from "../ui/useDragResize";

const maxWidth = () => Math.max(320, Math.floor(window.innerWidth * 0.7));

const STRATEGIES = [
  { name: "log_xz", label: "Log XZ (RMA 14, 5m)" },
  { name: "price_channel", label: "Price Channel (length 20, 5m)" },
];
const POLL_MS = 3000;

/** Live signals (paper): four strategies side by side on the live feed, each with its own position and P&L. No orders.
 * Slots 1 and 2 are the week's comparison (no exits); 3 and 4 default to the same strategies with 1:2 premium exits. */
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
  const tradesOpen = usePaperTradesStore((s) => s.open);
  const setTradesOpen = usePaperTradesStore((s) => s.setOpen);
  const { size, onPointerDown } = useDragResize("paper.panel.width", 384, 280, maxWidth, "x", -1);

  return (
    <aside
      className="relative flex h-full shrink-0 flex-col gap-3 overflow-y-auto border-l border-white/10 bg-[#0b0f14] p-3 text-xs text-white/80"
      style={{ width: size }}
    >
      <div
        role="separator"
        aria-label="Resize the paper panel"
        className="absolute inset-y-0 left-0 w-1.5 cursor-col-resize hover:bg-sky-500/30"
        onPointerDown={onPointerDown}
      />
      <div>
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-white">Live signals (paper)</h2>
          <button
            type="button"
            className="rounded border border-white/15 px-2 py-0.5"
            onClick={() => setTradesOpen(!tradesOpen)}
          >
            Trades
          </button>
        </div>
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
  const [strategy, setStrategy] = useState(SLOT_DEFAULTS[slot]?.strategy ?? "log_xz");
  const [exit, setExit] = useState(SLOT_DEFAULTS[slot]?.exit ?? "");
  const [paramsText, setParamsText] = useState("");
  const [week, setWeek] = useState<PaperWeek | null>(null);
  const day = status?.day ?? null;
  const closed = status?.trades?.length ?? 0;
  useEffect(() => {
    if (!day) return;
    let live = true;
    fetchPaperWeek(slot, day).then((w) => live && setWeek(w)).catch(() => live && setWeek(null));
    return () => {
      live = false;
    };
  }, [slot, day, closed]);
  const running = status?.state === "running";
  const summary = status?.summary ?? null;
  const parsed = parseParams(paramsText);
  const shownParams = status?.params && Object.keys(status.params).length ? JSON.stringify(status.params) : "defaults";

  return (
    <section className="flex flex-col gap-2 rounded border border-white/10 p-2" aria-label={`Paper strategy ${slot}`}>
      <h3 className="font-semibold text-white">Strategy {slot}</h3>
      <div className="flex flex-wrap items-center gap-2">
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
        <select
          aria-label={`Exit rule ${slot}`}
          className="rounded border border-white/15 bg-transparent px-2 py-1"
          value={running ? String(status?.params?.exit_rule ?? "") : exit}
          disabled={running}
          onChange={(e) => setExit(e.target.value)}
        >
          {EXIT_RULES.map((r) => (
            <option key={r.name} value={r.name} className="bg-[#0b0f14]">
              {r.label}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="rounded border border-white/15 px-3 py-1 disabled:opacity-40"
          disabled={disabled || (!running && parsed.params === undefined)}
          onClick={() => void (running ? stop(slot) : start(slot, strategy, startParams(parsed.params ?? {}, exit)))}
        >
          {running ? "Stop" : "Start"}
        </button>
      </div>
      {running ? (
        <div className="text-white/50">
          params: {shownParams}
          {exitLabel(status?.params) ? ` · exits: ${exitLabel(status?.params)}` : ""}
        </div>
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
        {status?.source === "replay" && (
          <span className="ml-2 text-amber-300">replay: started after the close, kept apart, not counted</span>
        )}
      </div>
      <div>Open P&amp;L: {markLine(status?.mark ?? null)}</div>
      <div>
        Day: net {formatRupees(summary?.net ?? null)} · trades {summary?.trades ?? 0} · wins {summary?.wins ?? 0} ·
        modelled legs {summary?.modelled_legs ?? 0}
      </div>
      <div className="text-white/60">Day exits: {exitCountsLine(summary?.exits)}</div>
      {week && (
        <div className="text-white/60">
          Week {week.week}: net {formatRupees(week.totals.net)} · trades {week.totals.trades} · exits {exitCountsLine(week.exits)}
        </div>
      )}
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
      <TradeReport rows={status?.report ?? []} counts={summary?.exits} />
    </section>
  );
}
