import { useEffect, useState } from "react";

import { formatRupees, markLine, paperRows } from "../paper/present";
import { usePaperStore } from "../store/paperStore";

const STRATEGIES = [{ name: "log_xz", label: "Log XZ (RMA 14, 5m)" }];
const DEFAULT_STRATEGY = "log_xz";
const POLL_MS = 3000;

/** Live signals (paper): start/stop a strategy on the live feed, the signals log and the open P&L. No orders. */
export function PaperPanel() {
  const status = usePaperStore((s) => s.status);
  const error = usePaperStore((s) => s.error);
  const refresh = usePaperStore((s) => s.refresh);
  const start = usePaperStore((s) => s.start);
  const stop = usePaperStore((s) => s.stop);
  const [strategy, setStrategy] = useState(DEFAULT_STRATEGY);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => void refresh(), POLL_MS);
    return () => window.clearInterval(id);
  }, [refresh]);

  const running = status?.state === "running";
  const disabled = status?.state === "disabled";
  const summary = status?.summary ?? null;

  return (
    <aside className="flex h-full w-96 shrink-0 flex-col gap-3 overflow-y-auto border-l border-white/10 bg-[#0b0f14] p-3 text-xs text-white/80">
      <div>
        <h2 className="text-sm font-semibold text-white">Live signals (paper)</h2>
        <p className="text-white/50">No orders are sent. Fills are paper: live quotes, or the model flagged as modelled.</p>
      </div>

      <div className="flex items-center gap-2">
        <select
          aria-label="Strategy"
          className="rounded border border-white/15 bg-transparent px-2 py-1"
          value={strategy}
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
          disabled={disabled}
          onClick={() => void (running ? stop() : start(strategy))}
        >
          {running ? "Stop" : "Start"}
        </button>
      </div>

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
      {disabled && <div className="text-yellow-300">The live feed is off, so paper trading cannot run.</div>}

      <table className="w-full text-left">
        <thead className="text-white/40">
          <tr>
            <th className="py-1">Time</th>
            <th>Side</th>
            <th>Index</th>
            <th>Contract</th>
            <th>Fill</th>
            <th>Reason / note</th>
          </tr>
        </thead>
        <tbody>
          {paperRows(status?.signals ?? []).map((r, i) => (
            <tr key={i} className="border-t border-white/5 align-top">
              <td className="py-1">{r.time}</td>
              <td>{r.side}</td>
              <td>{r.index}</td>
              <td>{r.contract}</td>
              <td>{r.fill}</td>
              <td className="text-white/50">{r.note}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </aside>
  );
}
