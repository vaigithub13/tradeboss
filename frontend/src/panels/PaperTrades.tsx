import { useMemo } from "react";

import { PAPER_SLOTS } from "../api/paper";
import { COLUMNS, cellText, filterRows, sortRows, toCsv, totals } from "../paper/trades";
import { rowKey, usePaperTradesStore } from "../store/paperTradesStore";
import { useDragResize } from "../ui/useDragResize";

const maxHeight = () => Math.max(200, window.innerHeight - 120);

/** Every paper trade over the slots (live days), one row each: a bottom drawer like Backtest results, drag its top
 * edge to resize, or expand it to full screen. A click draws the trade on the chart. */
export function PaperTrades() {
  const s = usePaperTradesStore();
  const { size, onPointerDown } = useDragResize("paper.trades.height", 320, 160, maxHeight, "y", -1);
  const shown = useMemo(() => sortRows(filterRows(s.rows, s.filter), s.sort.key, s.sort.dir), [s.rows, s.filter, s.sort]);
  const sum = totals(shown);
  if (!s.open) return null;

  const download = () => {
    const blob = new Blob([toCsv(shown)], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `paper-trades${s.filter.slot ? `-slot${s.filter.slot}` : ""}${s.filter.from ? `-${s.filter.from}` : ""}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <section
      aria-label="Paper trades"
      className={`flex flex-col border-t border-white/10 bg-[#0b0e14] text-xs text-white/80 ${s.full ? "fixed inset-0 z-50" : "relative shrink-0"}`}
      style={s.full ? undefined : { height: size }}
    >
      {!s.full && (
        <div
          role="separator"
          aria-label="Resize the trades view"
          className="absolute inset-x-0 -top-1 h-2 cursor-row-resize hover:bg-sky-500/30"
          onPointerDown={onPointerDown}
        />
      )}
      <div className="flex flex-wrap items-center gap-3 px-3 py-2">
        <h2 className="text-sm font-semibold text-white">Paper trades</h2>
        <label className="flex items-center gap-1">
          Slot
          <select aria-label="Slot filter" className="rounded border border-white/15 bg-transparent px-1 py-0.5"
            value={s.filter.slot} onChange={(e) => s.setFilter({ slot: e.target.value })}>
            <option value="" className="bg-[#0b0e14]">all</option>
            {PAPER_SLOTS.map((slot) => <option key={slot} value={slot} className="bg-[#0b0e14]">{slot}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-1">
          From
          <input aria-label="From date" type="date" className="rounded border border-white/15 bg-transparent px-1"
            value={s.filter.from} onChange={(e) => s.setFilter({ from: e.target.value })} />
        </label>
        <label className="flex items-center gap-1">
          To
          <input aria-label="To date" type="date" className="rounded border border-white/15 bg-transparent px-1"
            value={s.filter.to} onChange={(e) => s.setFilter({ to: e.target.value })} />
        </label>
        <button type="button" className="rounded border border-white/15 px-2 py-0.5" onClick={() => void s.load()}>
          {s.loading ? "Loading…" : "Reload"}
        </button>
        <button type="button" className="rounded border border-white/15 px-2 py-0.5" onClick={download} disabled={!shown.length}>
          CSV
        </button>
        {s.overlay && (
          <button type="button" className="rounded border border-white/15 px-2 py-0.5" onClick={s.clearSelection}>
            Clear chart
          </button>
        )}
        <span className="text-white/40">Live days only. ~ estimated through the delta; ≈ Nifty from the 1m candles.</span>
        <div className="ml-auto flex gap-2">
          <button type="button" className="rounded border border-white/15 px-2 py-0.5" onClick={s.toggleFull}>
            {s.full ? "Exit full screen" : "Full screen"}
          </button>
          <button type="button" className="rounded border border-white/15 px-2 py-0.5" onClick={() => s.setOpen(false)}>
            Close
          </button>
        </div>
      </div>
      {s.error && <div className="px-3 text-red-400">{s.error}</div>}
      <div className="min-h-0 flex-1 overflow-auto px-3 pb-2">
        <table className="w-max min-w-full text-left tabular-nums">
          <thead className="sticky top-0 z-10 bg-[#0b0e14] text-white/50">
            <tr>
              {COLUMNS.map((c) => (
                <th key={c.key} className={`whitespace-nowrap py-1 pr-3 font-normal ${c.numeric ? "text-right" : ""}`}>
                  <button type="button" className="hover:text-white" onClick={() => s.sortBy(c.key)}>
                    {c.label}
                    {s.sort.key === c.key ? (s.sort.dir === "asc" ? " ▲" : " ▼") : ""}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((row) => {
              const key = rowKey(row);
              return (
                <tr
                  key={key}
                  className={`cursor-pointer whitespace-nowrap border-t border-white/5 hover:bg-white/5 ${s.selected === key ? "bg-sky-900/40" : ""}`}
                  onClick={() => void s.select(row)}
                >
                  {COLUMNS.map((c) => (
                    <td
                      key={c.key}
                      className={`py-0.5 pr-3 ${c.numeric ? "text-right" : ""} ${
                        c.key === "net" ? ((row.net ?? 0) < 0 ? "text-red-400" : "text-emerald-400") : ""}`}
                    >
                      {cellText(row, c.key)}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
          <tfoot className="sticky bottom-0 bg-[#0b0e14] text-white">
            <tr className="border-t border-white/20">
              <td colSpan={COLUMNS.length} className="py-1">
                Trades {sum.trades} · wins {sum.wins} · net{" "}
                <span className={sum.net < 0 ? "text-red-400" : "text-emerald-400"}>{sum.net.toFixed(2)}</span> · average R{" "}
                {sum.avgR == null ? "—" : sum.avgR.toFixed(2)} · target {sum.exits.target} · stop {sum.exits.stop} · reversal{" "}
                {sum.exits.reversal} · square-off {sum.exits["square-off"]} · other {sum.exits.other}
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
    </section>
  );
}
