import { exitCountsLine, reportCells, type ExitCounts, type ReportRow } from "./present";

/** The trade report: one row per trade (paper day or backtest run). `~` marks levels estimated through the delta. */
export function TradeReport({ rows, counts, withDate = false, title = "Trade report" }: {
  rows: readonly ReportRow[];
  counts?: Partial<ExitCounts> | null;
  withDate?: boolean;
  title?: string;
}) {
  return (
    <div className="flex flex-col gap-1">
      <div className="text-white/60">
        {title}: {rows.length} trade{rows.length === 1 ? "" : "s"} · exits {exitCountsLine(counts)}
      </div>
      {rows.length > 0 && (
        <div className="max-h-64 overflow-auto">
          <table className="w-max min-w-full text-left tabular-nums" aria-label={title}>
            <thead className="sticky top-0 bg-[#0b0f14] text-white/40">
              <tr>
                {["Signal bar", "Trigger", "Fill", "Contract", "Entry", "Index stop / target", "Premium stop / target",
                  "Exit", "Exit prem", "Reason", "Net", "R"].map((h) => (
                  <th key={h} className="whitespace-nowrap py-1 pr-3 font-normal">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => {
                const c = reportCells(row, withDate);
                return (
                  <tr key={row.id ?? i} className="border-t border-white/5 whitespace-nowrap">
                    <td className="pr-3">{c.signal}</td>
                    <td className="pr-3 text-right">{c.trigger}</td>
                    <td className="pr-3">{c.fill}</td>
                    <td className="pr-3">{c.contract}</td>
                    <td className="pr-3 text-right">{c.entry}</td>
                    <td className="pr-3 text-right">{c.indexLevels}</td>
                    <td className="pr-3 text-right">{c.premiumLevels}</td>
                    <td className="pr-3">{c.exit}</td>
                    <td className="pr-3 text-right">{c.exitPremium}</td>
                    <td className="pr-3">{c.reason}</td>
                    <td className={`pr-3 text-right ${row.net != null && row.net < 0 ? "text-red-300" : "text-emerald-300"}`}>{c.net}</td>
                    <td className="pr-3 text-right">{c.r}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
