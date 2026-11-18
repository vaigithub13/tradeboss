import { usePineLiveStore } from "../store/pineLiveStore";

function istClock(unix: number): string {
  return new Date((unix + 19800) * 1000).toISOString().slice(11, 19);
}

export function PineAlertsPanel() {
  const log = usePineLiveStore((s) => s.log);
  const filter = usePineLiveStore((s) => s.logFilterScriptId);
  const setFilter = usePineLiveStore((s) => s.setLogFilter);
  const clearLog = usePineLiveStore((s) => s.clearLog);
  const scripts = usePineLiveStore((s) => s.scripts);
  const rows = filter ? log.filter((r) => r.scriptId === filter) : log;

  return (
    <aside
      className="flex h-full w-80 shrink-0 flex-col gap-2 overflow-y-auto border-l border-white/10 bg-[#0b0e14] p-3 text-xs text-white/80"
      data-testid="pine-alerts-panel"
    >
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-white">Alerts</h2>
        <button type="button" className="rounded border border-white/15 px-2 py-0.5" onClick={clearLog}>
          Clear
        </button>
      </div>
      <label className="flex items-center gap-2">
        Script
        <select
          className="flex-1 rounded border border-white/15 bg-transparent px-1 py-0.5"
          value={filter ?? ""}
          onChange={(e) => setFilter(e.target.value || null)}
          aria-label="Filter alerts by script"
        >
          <option value="">All</option>
          {scripts.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
      </label>
      <ul className="flex flex-col gap-1">
        {rows.length === 0 && <li className="text-white/40">No alerts today.</li>}
        {rows.map((r) => (
          <li key={r.id} className="rounded border border-white/10 px-2 py-1" data-testid="pine-alert-row">
            <div className="flex justify-between gap-2">
              <span className={r.side === "BUY" ? "text-emerald-300" : r.side === "SELL" ? "text-red-300" : "text-slate-300"}>
                {r.side}
              </span>
              <span className="text-white/50">{istClock(r.time)}</span>
            </div>
            <div className="text-white/70">
              {r.scriptName} · {r.symbol} {r.timeframe}
            </div>
            <div>
              {r.text} @ {r.price.toFixed(2)}
            </div>
          </li>
        ))}
      </ul>
    </aside>
  );
}
