import { useEffect, useMemo, useState } from "react";

import { fetchPaperWeek, type PaperStatus } from "../api/paper";
import { EXIT_RULES, formatRupees, SLOT_DEFAULTS, exitLabel, parseParams, startParams } from "./present";
import { playAlertSound } from "./sounds";
import {
  DEFAULT_DASHBOARD_FILTERS,
  computeKpis,
  dailyPnLMatrix,
  equitySeries,
  filterDashboardRows,
  holdMinutes,
  livePositionView,
  slotStats,
  todayIst,
  type DashboardFilters,
} from "./dashboard";
import { PaperEquityChart } from "./PaperEquityChart";
import { ResizableTable } from "./ResizableTable";
import {
  ACTIVE_PAPER_SLOTS,
  RETIRED_PAPER_SLOTS,
  STATUS_PILL_CLASS,
  isActivePaperSlot,
  slotLabel,
  slotStatusPill,
} from "./slots";
import { cellText, sortRows, toCsv, type ColumnKey, type PaperTradeRow } from "./trades";
import { OrderHelperBox } from "./OrderHelper";
import { useAlertStore } from "../store/alertStore";
import { usePaperStore } from "../store/paperStore";
import { usePaperTradesStore } from "../store/paperTradesStore";
import { PAPER_EQUITY_LEGEND_KEY, PAPER_FILTERS_KEY, readJson, writeJson } from "./storage";

const STRATEGIES = [
  { name: "log_xz", label: "Log XZ" },
  { name: "price_channel", label: "Price Channel" },
  { name: "dd_five_confirm", label: "DD 5-confirmation" },
];

type DashCol = { key: ColumnKey | "hold"; label: string; numeric?: boolean; width?: number };

const TRADE_COLS: DashCol[] = [
  { key: "date", label: "Date", width: 100 },
  { key: "entryTime", label: "In", width: 90 },
  { key: "exitTime", label: "Out", width: 90 },
  { key: "slot", label: "Slot", width: 50 },
  { key: "direction", label: "Side", width: 50 },
  { key: "contract", label: "Contract", width: 140 },
  { key: "entryPremium", label: "Entry", numeric: true, width: 100 },
  { key: "exitPremium", label: "Exit", numeric: true, width: 80 },
  { key: "lots", label: "Qty", width: 70 },
  { key: "net", label: "Net ₹", numeric: true, width: 90 },
  { key: "reason", label: "Exit reason", width: 100 },
  { key: "hold", label: "Hold min", numeric: true, width: 80 },
];

function Kpi({ label, value, tone }: { label: string; value: string; tone?: "pos" | "neg" | "neutral" }) {
  const color = tone === "pos" ? "text-emerald-400" : tone === "neg" ? "text-red-400" : "text-white";
  return (
    <div className="min-w-[8rem] flex-1 rounded border border-white/10 bg-white/[0.03] px-3 py-2" data-testid={`kpi-${label}`}>
      <div className="text-[10px] uppercase tracking-wide text-white/40">{label}</div>
      <div className={`font-mono text-xl font-semibold tabular-nums ${color}`}>{value}</div>
    </div>
  );
}

function toneOf(n: number | null | undefined): "pos" | "neg" | "neutral" {
  if (n == null || n === 0) return "neutral";
  return n > 0 ? "pos" : "neg";
}

/** Full-page Paper dashboard (Chart | Options | Paper). */
export function PaperDashboard() {
  const desk = usePaperStore((s) => s.status);
  const pollError = usePaperStore((s) => s.errors[""] ?? null);
  const start = usePaperStore((s) => s.start);
  const stop = usePaperStore((s) => s.stop);
  const tradeRows = usePaperTradesStore((s) => s.rows);
  const tradesLoading = usePaperTradesStore((s) => s.loading);
  const tradesError = usePaperTradesStore((s) => s.error);
  const loadTrades = usePaperTradesStore((s) => s.load);
  const [filters, setFilters] = useState<DashboardFilters>(() => ({
    ...DEFAULT_DASHBOARD_FILTERS,
    ...readJson(PAPER_FILTERS_KEY, {}),
  }));
  const [sort, setSort] = useState<{ key: ColumnKey; dir: "asc" | "desc" }>({ key: "entryTime", dir: "desc" });
  const [legend, setLegend] = useState<Record<string, boolean>>(() =>
    readJson(PAPER_EQUITY_LEGEND_KEY, Object.fromEntries(ACTIVE_PAPER_SLOTS.map((s) => [s, true]))),
  );
  const [weekNets, setWeekNets] = useState<Record<string, number>>({});
  const today = todayIst();

  useEffect(() => {
    void loadTrades();
  }, [loadTrades]);

  useEffect(() => {
    writeJson(PAPER_FILTERS_KEY, filters);
  }, [filters]);

  useEffect(() => {
    let live = true;
    const day = desk?.slots.find((s) => s.day)?.day ?? today;
    void Promise.all(
      ACTIVE_PAPER_SLOTS.map(async (slot) => {
        try {
          const w = await fetchPaperWeek(slot, day);
          return [slot, w.totals.net] as const;
        } catch {
          return [slot, 0] as const;
        }
      }),
    ).then((pairs) => {
      if (!live) return;
      setWeekNets(Object.fromEntries(pairs));
    });
    return () => {
      live = false;
    };
  }, [desk, today]);

  const allRows = tradeRows;
  const activeRows = useMemo(() => allRows.filter((r) => isActivePaperSlot(r.slot)), [allRows]);
  const filtered = useMemo(() => filterDashboardRows(activeRows, filters), [activeRows, filters]);
  const sorted = useMemo(() => sortRows(filtered, sort.key, sort.dir), [filtered, sort]);
  const kpis = useMemo(() => computeKpis(activeRows, { today, weekNets }), [activeRows, today, weekNets]);
  const daily = useMemo(() => dailyPnLMatrix(activeRows), [activeRows]);
  const equity = useMemo(() => equitySeries(activeRows), [activeRows]);
  const bySlot = useMemo(() => {
    const map = new Map((desk?.slots ?? []).map((s) => [s.slot, s]));
    return ACTIVE_PAPER_SLOTS.map((slot) => map.get(slot) ?? null);
  }, [desk]);

  const offline = Boolean(pollError) || tradesError != null;

  const download = () => {
    const blob = new Blob([toCsv(sorted)], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "paper-dashboard-trades.csv";
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="flex h-full min-h-0 flex-col overflow-y-auto bg-[#070a0f] text-xs text-white/80" data-testid="paper-dashboard">
      <div className="mx-auto flex w-full max-w-[1400px] flex-col gap-4 p-4">
        <div className="flex flex-wrap items-end justify-between gap-2">
          <div>
            <h1 className="text-lg font-semibold text-white">Paper dashboard</h1>
            <p className="text-white/45">Forward-test desk · no real orders · LIVE_TRADING stays false</p>
          </div>
          {offline && (
            <div className="rounded border border-amber-500/40 bg-amber-950/40 px-3 py-1 text-amber-200" data-testid="paper-offline">
              backend offline
            </div>
          )}
        </div>

        {/* KPIs */}
        <section className="flex flex-wrap gap-2" aria-label="Paper KPIs">
          <Kpi label="Today net ₹" value={formatRupees(kpis.todayNet)} tone={toneOf(kpis.todayNet)} />
          <Kpi label="This week net ₹" value={formatRupees(kpis.weekNet)} tone={toneOf(kpis.weekNet)} />
          <Kpi label="Forward-test total ₹" value={formatRupees(kpis.forwardNet)} tone={toneOf(kpis.forwardNet)} />
          <Kpi label="Win %" value={kpis.winPct == null ? "—" : `${kpis.winPct}%`} tone="neutral" />
          <Kpi label="Trades today" value={String(kpis.tradesToday)} tone="neutral" />
          <Kpi
            label="Max drawdown (forward)"
            value={kpis.maxDrawdown == null ? "—" : formatRupees(-Math.abs(kpis.maxDrawdown))}
            tone={kpis.maxDrawdown ? "neg" : "neutral"}
          />
        </section>

        {/* Slot cards */}
        <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" aria-label="Active paper slots">
          {ACTIVE_PAPER_SLOTS.map((slot, i) => (
            <SlotCard
              key={slot}
              slot={slot}
              status={bySlot[i] ?? null}
              stats={slotStats(activeRows, slot, today)}
              onStart={(strategy, params) => void start(slot, strategy, params)}
              onStop={() => void stop(slot)}
            />
          ))}
        </section>

        {RETIRED_PAPER_SLOTS.length > 0 && (
          <p className="text-[11px] text-white/35" data-testid="retired-note">
            Retired (history only, not shown as active cards):{" "}
            {RETIRED_PAPER_SLOTS.map((s) => `${s} ${slotLabel(s)}`).join(" · ")}
          </p>
        )}

        {/* Equity */}
        <section className="rounded border border-white/10 bg-white/[0.02] p-3" aria-label="Equity curve">
          <h2 className="mb-2 text-sm font-semibold text-white">Equity curve</h2>
          {tradesLoading ? (
            <div className="py-10 text-center text-white/40">Loading…</div>
          ) : (
            <PaperEquityChart
              series={equity}
              visible={legend}
              onToggle={(slot) => {
                setLegend((prev) => {
                  const next = { ...prev, [slot]: prev[slot] === false };
                  writeJson(PAPER_EQUITY_LEGEND_KEY, next);
                  return next;
                });
              }}
            />
          )}
        </section>

        {/* Daily P&L */}
        <section className="rounded border border-white/10 bg-white/[0.02] p-3" aria-label="Daily P&L">
          <h2 className="mb-2 text-sm font-semibold text-white">Daily P&amp;L</h2>
          {!daily.length ? (
            <div className="py-6 text-center text-white/40">No days yet</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[32rem] text-left tabular-nums">
                <thead className="text-white/45">
                  <tr>
                    <th className="py-1 pr-3 font-normal">Date</th>
                    {ACTIVE_PAPER_SLOTS.map((s) => (
                      <th key={s} className="py-1 pr-3 text-right font-normal">
                        {s}
                      </th>
                    ))}
                    <th className="py-1 text-right font-normal">Total</th>
                  </tr>
                </thead>
                <tbody>
                  {daily.map((d) => (
                    <tr key={d.date} className="border-t border-white/5">
                      <td className="py-1 pr-3">{d.date}</td>
                      {ACTIVE_PAPER_SLOTS.map((s) => {
                        const v = d.bySlot[s] ?? 0;
                        return (
                          <td key={s} className={`py-1 pr-3 text-right ${v < 0 ? "text-red-400" : v > 0 ? "text-emerald-400" : "text-white/40"}`}>
                            {v === 0 ? "—" : formatRupees(v)}
                          </td>
                        );
                      })}
                      <td className={`py-1 text-right ${d.total < 0 ? "text-red-400" : "text-emerald-400"}`}>{formatRupees(d.total)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {/* Trades table */}
        <section className="flex min-h-[20rem] flex-col rounded border border-white/10 bg-white/[0.02] p-3" aria-label="Trades">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <h2 className="text-sm font-semibold text-white">Trades</h2>
            <label className="flex items-center gap-1">
              Slot
              <select
                aria-label="Slot filter"
                className="rounded border border-white/15 bg-transparent px-1 py-0.5"
                value={filters.slot}
                onChange={(e) => setFilters((f) => ({ ...f, slot: e.target.value }))}
              >
                <option value="" className="bg-[#0b0e14]">all active</option>
                {ACTIVE_PAPER_SLOTS.map((s) => (
                  <option key={s} value={s} className="bg-[#0b0e14]">{s}</option>
                ))}
              </select>
            </label>
            <label className="flex items-center gap-1">
              From
              <input type="date" aria-label="From date" className="rounded border border-white/15 bg-transparent px-1"
                value={filters.from} onChange={(e) => setFilters((f) => ({ ...f, from: e.target.value }))} />
            </label>
            <label className="flex items-center gap-1">
              To
              <input type="date" aria-label="To date" className="rounded border border-white/15 bg-transparent px-1"
                value={filters.to} onChange={(e) => setFilters((f) => ({ ...f, to: e.target.value }))} />
            </label>
            <label className="flex items-center gap-1">
              Side
              <select aria-label="Side filter" className="rounded border border-white/15 bg-transparent px-1 py-0.5"
                value={filters.side} onChange={(e) => setFilters((f) => ({ ...f, side: e.target.value as DashboardFilters["side"] }))}>
                <option value="" className="bg-[#0b0e14]">all</option>
                <option value="CE" className="bg-[#0b0e14]">CE</option>
                <option value="PE" className="bg-[#0b0e14]">PE</option>
              </select>
            </label>
            <label className="flex items-center gap-1">
              Result
              <select aria-label="Outcome filter" className="rounded border border-white/15 bg-transparent px-1 py-0.5"
                value={filters.outcome} onChange={(e) => setFilters((f) => ({ ...f, outcome: e.target.value as DashboardFilters["outcome"] }))}>
                <option value="" className="bg-[#0b0e14]">all</option>
                <option value="win" className="bg-[#0b0e14]">win</option>
                <option value="loss" className="bg-[#0b0e14]">loss</option>
              </select>
            </label>
            <button type="button" className="rounded border border-white/15 px-2 py-0.5" onClick={() => void loadTrades()}>
              {tradesLoading ? "Loading…" : "Reload"}
            </button>
            <button type="button" className="rounded border border-white/15 px-2 py-0.5" disabled={!sorted.length} onClick={download}>
              CSV
            </button>
          </div>
          {!sorted.length ? (
            <div className="py-8 text-center text-white/40">{tradesLoading ? "Loading…" : "No trades match"}</div>
          ) : (
            <ResizableTable
              storageKey="paper.dashboard.colWidths"
              columns={TRADE_COLS.map((c) => ({
                ...c,
                onSort:
                  c.key === "hold"
                    ? undefined
                    : () =>
                        setSort((s) => ({
                          key: c.key as ColumnKey,
                          dir: s.key === c.key && s.dir === "desc" ? "asc" : "desc",
                        })),
                sorted: c.key !== "hold" && sort.key === c.key ? sort.dir : null,
              }))}
            >
              <tbody>
                {sorted.map((row) => (
                  <TradeRow key={`${row.slot}:${row.entry_time}`} row={row} />
                ))}
              </tbody>
            </ResizableTable>
          )}
        </section>

        {/* Details per slot */}
        <section className="space-y-2" aria-label="Slot details">
          <h2 className="text-sm font-semibold text-white">Details</h2>
          {ACTIVE_PAPER_SLOTS.map((slot, i) => (
            <SlotDetails key={slot} slot={slot} status={bySlot[i] ?? null} onStart={start} onStop={stop} />
          ))}
          <SoundTestRow />
        </section>
      </div>
    </div>
  );
}

function TradeRow({ row }: { row: PaperTradeRow }) {
  const hold = holdMinutes(row);
  return (
    <tr className="border-t border-white/5">
      {TRADE_COLS.map((c) => (
        <td
          key={c.key}
          className={`whitespace-nowrap py-0.5 pr-3 ${c.numeric ? "text-right" : ""} ${
            c.key === "net" ? ((row.net ?? 0) < 0 ? "text-red-400" : "text-emerald-400") : ""
          }`}
        >
          {c.key === "hold" ? (hold == null ? "—" : hold.toFixed(0)) : cellText(row, c.key)}
        </td>
      ))}
    </tr>
  );
}

function SlotCard({
  slot,
  status,
  stats,
  onStart,
  onStop,
}: {
  slot: string;
  status: PaperStatus | null;
  stats: ReturnType<typeof slotStats>;
  onStart: (strategy: string, params: Record<string, unknown>) => void;
  onStop: () => void;
}) {
  const pill = slotStatusPill(status);
  const live = livePositionView(status);
  const running = status?.state === "running" || status?.state === "starting";
  const def = SLOT_DEFAULTS[slot];

  return (
    <article className="flex flex-col gap-2 rounded border border-white/10 bg-white/[0.03] p-3" data-testid={`dash-slot-${slot}`}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="truncate font-semibold text-white" title={slotLabel(slot)}>
            {slot} · {slotLabel(slot)}
          </h3>
          <p className="truncate text-[11px] text-white/45">{status?.strategy ?? def?.strategy ?? "—"}</p>
        </div>
        <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] uppercase ${STATUS_PILL_CLASS[pill]}`}>{pill}</span>
      </div>
      <div className="grid grid-cols-2 gap-1 tabular-nums">
        <div>
          <div className="text-[10px] text-white/40">Today</div>
          <div className={stats.todayNet < 0 ? "text-red-400" : "text-emerald-400"}>{formatRupees(stats.todayNet)}</div>
        </div>
        <div>
          <div className="text-[10px] text-white/40">Total</div>
          <div className={stats.totalNet < 0 ? "text-red-400" : "text-emerald-400"}>{formatRupees(stats.totalNet)}</div>
        </div>
        <div className="text-white/60">
          {stats.trades} / {stats.wins}W · {stats.winPct == null ? "—" : `${stats.winPct}%`}
        </div>
        <div className="text-right text-white/60">
          avg {stats.avgWin == null ? "—" : formatRupees(stats.avgWin)} / {stats.avgLoss == null ? "—" : formatRupees(stats.avgLoss)}
        </div>
      </div>
      <div className="flex flex-wrap gap-1">
        {Object.entries(stats.exits).map(([k, n]) => (
          <span key={k} className="rounded bg-white/5 px-1.5 py-0.5 text-[10px] text-white/55">
            {k} {n}
          </span>
        ))}
      </div>
      {live && (
        <div className="rounded bg-black/30 px-2 py-1.5 text-[11px]" data-testid={`dash-live-${slot}`}>
          <div className="flex justify-between">
            <span>{live.side} {live.strike}</span>
            <span className={live.unrealised != null && live.unrealised < 0 ? "text-red-400" : "text-emerald-400"}>
              {formatRupees(live.unrealised)}
            </span>
          </div>
          <div className="mt-0.5 flex justify-between text-white/50">
            <span>Entry {live.entry?.toFixed(2) ?? "—"}</span>
            <span title="LTP not in status API">LTP —</span>
          </div>
          <div className="flex justify-between text-white/50">
            <span>SL {live.stop?.toFixed(2) ?? "—"}</span>
            <span>TG {live.target?.toFixed(2) ?? "—"}</span>
          </div>
        </div>
      )}
      <button
        type="button"
        className="mt-auto rounded border border-white/15 px-2 py-1 disabled:opacity-40"
        disabled={status?.state === "disabled"}
        onClick={() => {
          if (running) onStop();
          else if (def) onStart(def.strategy, startParams(def.params ? JSON.parse(def.params) : {}, def.exit));
        }}
      >
        {running ? (status?.state === "starting" ? "Starting…" : "Stop") : "Start"}
      </button>
    </article>
  );
}

function SlotDetails({
  slot,
  status,
  onStart,
  onStop,
}: {
  slot: string;
  status: PaperStatus | null;
  onStart: (slot: string, strategy: string, params: Record<string, unknown>) => Promise<void>;
  onStop: (slot: string) => Promise<void>;
}) {
  const def = SLOT_DEFAULTS[slot];
  const [strategy, setStrategy] = useState(def?.strategy ?? "price_channel");
  const [exit, setExit] = useState(def?.exit ?? "");
  const [paramsText, setParamsText] = useState(def?.params ?? "");
  const running = status?.state === "running" || status?.state === "starting";
  const parsed = parseParams(paramsText);
  const shownParams = status?.params && Object.keys(status.params).length ? JSON.stringify(status.params, null, 2) : "defaults";

  return (
    <details className="rounded border border-white/10 bg-white/[0.02] p-2" data-testid={`details-${slot}`}>
      <summary className="cursor-pointer font-medium text-white">
        Slot {slot} · {slotLabel(slot)} — params, shadow, start source
      </summary>
      <div className="mt-2 flex flex-col gap-2">
        <div className="flex flex-wrap gap-2">
          <select
            className="rounded border border-white/15 bg-transparent px-2 py-1"
            value={running && status?.strategy ? status.strategy : strategy}
            disabled={running}
            onChange={(e) => setStrategy(e.target.value)}
          >
            {STRATEGIES.map((s) => (
              <option key={s.name} value={s.name} className="bg-[#0b0e14]">{s.label}</option>
            ))}
          </select>
          <select
            className="rounded border border-white/15 bg-transparent px-2 py-1"
            value={running ? String(status?.params?.exit_rule ?? "") : exit}
            disabled={running}
            onChange={(e) => setExit(e.target.value)}
          >
            {EXIT_RULES.map((r) => (
              <option key={r.name} value={r.name} className="bg-[#0b0e14]">{r.label}</option>
            ))}
          </select>
          <button
            type="button"
            className="rounded border border-white/15 px-3 py-1"
            disabled={status?.state === "disabled" || (!running && parsed.params === undefined)}
            onClick={() => void (running ? onStop(slot) : onStart(slot, strategy, startParams(parsed.params ?? {}, exit)))}
          >
            {running ? "Stop" : "Start"}
          </button>
        </div>
        {running ? (
          <pre className="max-h-40 overflow-auto rounded bg-black/40 p-2 font-mono text-[11px] text-white/70">{shownParams}</pre>
        ) : (
          <textarea
            aria-label={`Params ${slot}`}
            className="min-h-[4rem] rounded border border-white/15 bg-transparent p-2 font-mono"
            value={paramsText}
            onChange={(e) => setParamsText(e.target.value)}
          />
        )}
        {parsed.error && !running && <div className="text-yellow-300">{parsed.error}</div>}
        <div className="text-white/55">
          state: {status?.state ?? "—"}
          {status?.ended_by ? ` (${status.ended_by})` : ""}
          {status?.start_source ? ` · started: ${status.start_source}` : ""}
          {exitLabel(status?.params) ? ` · exits: ${exitLabel(status?.params)}` : ""}
        </div>
        <div className="text-white/55">
          Shadow net {formatRupees(status?.summary?.shadow_net ?? null)} · diff{" "}
          {formatRupees(status?.summary?.shadow_diff ?? null)}
        </div>
        {(slot === "4" || slot === "5" || slot === "6") && (
          <OrderHelperBox
            open={status?.position ?? null}
            slot={slot}
            trailing={slot === "5" || slot === "6" ? "Trailing stop updates live on paper's SL." : undefined}
          />
        )}
      </div>
    </details>
  );
}

function SoundTestRow() {
  const soundOn = useAlertStore((s) => s.soundOn);
  const volume = useAlertStore((s) => s.volume);
  const slots = useAlertStore((s) => s.slots);
  const setSlot = useAlertStore((s) => s.setSlot);
  return (
    <div className="rounded border border-white/10 bg-white/[0.02] p-3" data-testid="sound-tests">
      <div className="mb-2 font-medium text-white">Alert tests</div>
      <div className="flex flex-wrap gap-3">
        {ACTIVE_PAPER_SLOTS.map((slot) => (
          <label key={slot} className="flex items-center gap-1">
            <input type="checkbox" checked={slots[slot] !== false} onChange={(e) => setSlot(slot, e.target.checked)} />
            Slot {slot}
          </label>
        ))}
      </div>
      <div className="mt-2 flex gap-2">
        <button type="button" className="rounded border border-white/15 px-2 py-0.5 disabled:opacity-40" disabled={!soundOn}
          onClick={() => playAlertSound("entry", volume)}>Test entry sound</button>
        <button type="button" className="rounded border border-white/15 px-2 py-0.5 disabled:opacity-40" disabled={!soundOn}
          onClick={() => playAlertSound("exit", volume)}>Test exit sound</button>
      </div>
    </div>
  );
}
