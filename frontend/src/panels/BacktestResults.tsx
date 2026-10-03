import { useEffect, useMemo, useState } from "react";

import type { BacktestRun, EquityPoint, ResultTrade } from "../api/backtests";
import { fetchRun } from "../api/backtests";
import {
  compareSelection,
  compareWarningLines,
  runLabel,
  sortTrades,
  warningLines,
  type SortKey,
} from "../backtest/present";
import type { Timeframe } from "../api/client";
import { useBacktestStore } from "../store/backtestStore";
import { useChartStore } from "../store/chartStore";

const inr = (n: number | null | undefined): string =>
  n == null ? "—" : n.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const pct = (n: number | null | undefined): string => (n == null ? "—" : `${(n * 100).toFixed(2)}%`);

export function BacktestResults() {
  const active = useBacktestStore((s) => s.active);
  const comparing = useBacktestStore((s) => s.comparing);
  const compareIds = useBacktestStore((s) => s.compareIds);
  const runs = useBacktestStore((s) => s.runs);
  const refresh = useBacktestStore((s) => s.refresh);
  const setComparing = useBacktestStore((s) => s.setComparing);

  const running = active?.status === "queued" || active?.status === "running";
  useEffect(() => {
    if (!running) return;
    const id = window.setInterval(() => void refresh(), 700);
    return () => window.clearInterval(id);
  }, [running, refresh]);

  if (comparing) {
    const picked = compareSelection(compareIds);
    return (
      <section className="flex h-80 shrink-0 flex-col gap-2 overflow-auto border-t border-white/10 bg-[#0b0e14] p-3 text-xs text-white/80">
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-semibold text-white">Compare</h2>
          <button type="button" className="text-sky-300" onClick={() => setComparing(false)}>Close</button>
        </div>
        {picked.ok ? <CompareView ids={picked.ids} labels={runs} /> : <p>{picked.reason}</p>}
      </section>
    );
  }

  if (!active) return null;
  return (
    <section className="flex h-80 shrink-0 flex-col gap-2 overflow-auto border-t border-white/10 bg-[#0b0e14] p-3 text-xs text-white/80">
      <div className="flex items-center gap-3">
        <h2 className="text-sm font-semibold text-white">{runLabel(active.config)}</h2>
        <span className="text-white/40">{active.status}</span>
        {active.model_version && <span className="text-white/40">{active.model_version}</span>}
      </div>
      {running && (
        <p>
          {active.progress.phase} {active.progress.done}/{active.progress.total || "…"}
        </p>
      )}
      {active.error && <p className="text-red-300">{active.error}</p>}
      <WarningList lines={warningLines(active.warnings, active.git_dirty)} />
      {active.status === "done" && active.result && <DoneRun run={active} />}
    </section>
  );
}

function WarningList({ lines }: { lines: string[] }) {
  if (lines.length === 0) return null;
  return (
    <ul className="flex flex-col gap-1 rounded border border-amber-400/40 bg-amber-400/10 p-2 text-amber-100" role="status">
      {lines.map((line) => (
        <li key={line}>{line}</li>
      ))}
    </ul>
  );
}

function DoneRun({ run }: { run: BacktestRun }) {
  const result = run.result;
  if (!result) return null;
  const index = result.summary.index;
  const option = result.summary.option;
  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-2 gap-3">
        <Side title="Index" net={index.net_pnl} trades={index.trades} win={index.win_rate} dd={index.max_drawdown} extra={`Points ${inr(index.points)}`} />
        {option && (
          <Side
            title="Options"
            net={option.net_pnl}
            trades={option.trades}
            win={option.win_rate}
            dd={option.max_drawdown}
            extra={`Real ${option.real_fills ?? 0} fills ${inr(option.real_pnl)} · Modelled ${option.modelled_fills ?? 0} fills ${inr(option.modelled_pnl)}`}
          />
        )}
      </div>
      <MoneyTotals run={run} />
      <div className="grid grid-cols-2 gap-3">
        <Curve title="Equity (₹)" series={[{ name: "Index", points: result.equity.index, color: "#7dd3fc" }, ...(option ? [{ name: "Options", points: result.equity.option, color: "#fbbf24" }] : [])]} field="equity" />
        <Curve title="Drawdown (₹)" series={[{ name: "Index", points: result.equity.index, color: "#7dd3fc" }, ...(option ? [{ name: "Options", points: result.equity.option, color: "#fbbf24" }] : [])]} field="drawdown" />
      </div>
      <Breakdowns run={run} />
      <Trades run={run} />
    </div>
  );
}

function Side({ title, net, trades, win, dd, extra }: { title: string; net: number; trades: number; win: number | null; dd: number; extra: string }) {
  return (
    <div className="rounded border border-white/10 p-2">
      <h3 className="font-semibold text-white">{title}</h3>
      <p>Net {inr(net)}</p>
      <p>Trades {trades} · Win {pct(win)}</p>
      <p>Max drawdown {inr(dd)}</p>
      <p className="text-white/50">{extra}</p>
    </div>
  );
}

function MoneyTotals({ run }: { run: BacktestRun }) {
  const result = run.result;
  if (!result) return null;
  const side = result.summary.option ?? result.summary.index;
  const trades = result.trades;
  const gross = result.summary.option
    ? trades.reduce((sum, trade) => sum + (trade.option?.gross_pnl ?? 0), 0)
    : (side.gross_pnl ?? 0);
  return (
    <p>
      Gross {inr(gross)} · Charges {inr(side.total_charges)} · Slippage {inr(side.total_slippage)} · Net {inr(side.net_pnl)}
    </p>
  );
}

function Curve({ title, series, field }: { title: string; series: { name: string; points: EquityPoint[]; color: string }[]; field: "equity" | "drawdown" }) {
  const width = 360;
  const height = 100;
  const all = series.flatMap((item) => item.points.map((point) => point[field]));
  if (all.length === 0) return null;
  const min = Math.min(0, ...all);
  const max = Math.max(0, ...all);
  const span = max - min || 1;
  const longest = Math.max(...series.map((item) => item.points.length), 1);
  const path = (points: EquityPoint[]): string =>
    points.map((point, i) => {
      const x = (i / Math.max(longest - 1, 1)) * (width - 8) + 4;
      const y = height - 8 - ((point[field] - min) / span) * (height - 16);
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(" ");
  return (
    <figure>
      <figcaption className="mb-1 text-white/60">{title}</figcaption>
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full rounded border border-white/10 bg-black/20">
        {series.map((item) => (
          <path key={item.name} d={path(item.points)} fill="none" stroke={item.color} strokeWidth="1.5" />
        ))}
      </svg>
      <p className="text-white/40">{series.map((item) => item.name).join(" · ")}</p>
    </figure>
  );
}

function Breakdowns({ run }: { run: BacktestRun }) {
  const blocks = run.result?.breakdowns;
  if (!blocks) return null;
  return (
    <div className="grid grid-cols-2 gap-3">
      <Block title="Weekday" rows={blocks.weekday} />
      <Block title="Time of day" rows={blocks.time_of_day} />
      <Block title="DTE" rows={blocks.dte} />
      <div>
        <h3 className="mb-1 font-semibold text-white">Event flags</h3>
        <ul>
          {Object.entries(blocks.flags).map(([name, block]) => (
            <li key={name}>
              {name}: {block.flagged.trades} trades, {inr(block.flagged.net_pnl)}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function Block({ title, rows }: { title: string; rows: Record<string, { trades: number; net_pnl: number }> }) {
  return (
    <div>
      <h3 className="mb-1 font-semibold text-white">{title}</h3>
      <ul>
        {Object.entries(rows).map(([name, row]) => (
          <li key={name}>{name}: {row.trades} · {inr(row.net_pnl)}</li>
        ))}
      </ul>
    </div>
  );
}

function Trades({ run }: { run: BacktestRun }) {
  const trades = run.result?.trades ?? [];
  const [key, setKey] = useState<SortKey>("entry_time");
  const [dir, setDir] = useState<"asc" | "desc">("asc");
  const selected = useBacktestStore((s) => s.selectedTradeId);
  const selectTrade = useBacktestStore((s) => s.selectTrade);
  const showAround = useChartStore((s) => s.showAround);
  const setSymbol = useChartStore((s) => s.setSymbol);
  const setTimeframe = useChartStore((s) => s.setTimeframe);
  const ordered = useMemo(() => sortTrades(trades, key, dir), [trades, key, dir]);

  const jump = async (trade: ResultTrade): Promise<void> => {
    if (useChartStore.getState().symbol !== run.config.symbol) await setSymbol(run.config.symbol);
    if (useChartStore.getState().timeframe !== run.config.timeframe) {
      await setTimeframe(run.config.timeframe as Timeframe);
    }
    await showAround(trade.entry_time);
    selectTrade(trade.id, trade.entry_time);
  };

  const sortBy = (next: SortKey): void => {
    if (key === next) setDir(dir === "asc" ? "desc" : "asc");
    else {
      setKey(next);
      setDir("asc");
    }
  };

  return (
    <div>
      <table className="w-full text-left">
        <thead>
          <tr className="text-white/50">
            <th><button type="button" onClick={() => sortBy("entry_time")}>Entry</button></th>
            <th><button type="button" onClick={() => sortBy("direction")}>Side</button></th>
            <th>Contract</th>
            <th>Premiums</th>
            <th>Source</th>
            <th><button type="button" onClick={() => sortBy("net_pnl")}>Net</button></th>
          </tr>
        </thead>
        <tbody>
          {ordered.map((trade) => (
            <tr
              key={trade.id}
              className={selected === trade.id ? "bg-white/10" : undefined}
              onClick={() => void jump(trade)}
            >
              <td>{new Date(trade.entry_time * 1000).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", hour12: false })}</td>
              <td>{trade.direction}</td>
              <td>{trade.option ? `${trade.option.contract.kind} ${trade.option.contract.strike} ${trade.option.contract.expiry}` : "—"}</td>
              <td>{trade.option ? `${trade.option.entry_premium} → ${trade.option.exit_premium}` : `${trade.entry_price} → ${trade.exit_price}`}</td>
              <td>{trade.option ? `${trade.option.entry_source}/${trade.option.exit_source}` : "index"}</td>
              <td>{inr(trade.option?.net_pnl ?? trade.net_pnl)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function TradeCard() {
  const active = useBacktestStore((s) => s.active);
  const selected = useBacktestStore((s) => s.selectedTradeId);
  const trade = active?.result?.trades.find((item) => item.id === selected);
  if (!trade?.option) return null;
  const option = trade.option;
  const charges = Object.entries({ ...option.charges_buy, ...option.charges_sell });
  return (
    <div className="absolute bottom-3 right-3 z-20 w-64 rounded border border-white/15 bg-[#0b0e14]/95 p-2 text-[11px] text-white/80">
      <p className="font-semibold text-white">
        {option.contract.kind} {option.contract.strike} exp {option.contract.expiry}
      </p>
      <p>Premium {option.entry_premium} → {option.exit_premium}</p>
      <p>Fill {option.entry_fill} → {option.exit_fill}</p>
      <p>{option.entry_source} / {option.exit_source}</p>
      <p>Gross {inr(option.gross_pnl)}</p>
      <p>Charges {inr(option.charges_total)} {charges.map(([name, value]) => `${name} ${inr(value)}`).join(", ")}</p>
      <p>Slippage {inr(option.slippage_cost)}</p>
      <p>Net {inr(option.net_pnl)}</p>
    </div>
  );
}

function CompareView({ ids, labels }: { ids: string[]; labels: BacktestRun[] }) {
  const [runs, setRuns] = useState<BacktestRun[]>([]);
  useEffect(() => {
    let cancelled = false;
    void Promise.all(ids.map((id) => fetchRun(id))).then((rows) => {
      if (!cancelled) setRuns(rows);
    });
    return () => {
      cancelled = true;
    };
  }, [ids]);
  if (runs.length !== ids.length) return <p>Loading compare…</p>;
  const lines = compareWarningLines(runs.map((run) => ({ label: runLabel(run.config), git_dirty: run.git_dirty, warnings: run.warnings })));
  const colors = ["#7dd3fc", "#fbbf24", "#c4b5fd"];
  return (
    <div className="flex flex-col gap-3">
      <WarningList lines={lines} />
      <table className="w-full text-left">
        <thead>
          <tr className="text-white/50">
            <th>Run</th>
            <th>Index net</th>
            <th>Option net</th>
            <th>Option max drawdown</th>
            <th>Trades</th>
            <th>Win</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => {
            const option = run.result?.summary.option;
            const index = run.result?.summary.index;
            return (
              <tr key={run.id}>
                <td>{runLabel(run.config)}{labels.find((item) => item.id === run.id)?.git_dirty ? "" : ""}</td>
                <td>{inr(index?.net_pnl)}</td>
                <td>{inr(option?.net_pnl)}</td>
                <td>{inr(option?.max_drawdown)}</td>
                <td>{option?.trades ?? index?.trades}</td>
                <td>{pct(option?.win_rate ?? index?.win_rate)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <Curve
        title="Equity (₹)"
        field="equity"
        series={runs.map((run, i) => ({
          name: runLabel(run.config),
          points: run.result?.summary.option ? (run.result?.equity.option ?? []) : (run.result?.equity.index ?? []),
          color: colors[i] ?? "#fff",
        }))}
      />
    </div>
  );
}
