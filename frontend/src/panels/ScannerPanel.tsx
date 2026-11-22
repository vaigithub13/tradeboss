import { useCallback, useEffect, useMemo, useState } from "react";

import {
  fetchScannerHeatmap,
  fetchScannerReport,
  fetchScannerSettings,
  fetchScannerSignals,
  fetchScannerStatus,
  fetchScannerTrades,
  fetchRankings,
  fetchUniverses,
  saveScannerSettings,
  scanNow,
  type HeatRow,
  type RankPair,
  type ScannerSettings,
  type ScannerSignal,
  type ScannerStatus,
  type ScannerTrade,
} from "../api/scanner";
import { ScannerChart } from "../scanner/ScannerChart";
import { decisionClass, sortRows, verdictClass } from "../scanner/sort";
import { enableScannerSound } from "../scanner/useAlerts";

const COLUMNS: { key: string; label: string }[] = [
  { key: "symbol", label: "Stock" },
  { key: "setup", label: "Setup" },
  { key: "side", label: "Side" },
  { key: "status", label: "Status" },
  { key: "entry", label: "Entry" },
  { key: "stop", label: "SL" },
  { key: "target", label: "Target" },
  { key: "qty", label: "Qty" },
  { key: "rupee_risk", label: "₹ risk" },
  { key: "rr", label: "R:R" },
  { key: "target_pct", label: "Target %" },
  { key: "stop_pct", label: "Stop %" },
  { key: "win_rate", label: "History" },
  { key: "verdict", label: "Verdict" },
  { key: "decision", label: "Risk" },
];

function n(value: number | null | undefined, digits = 2): string {
  if (value == null || Number.isNaN(value)) return "—";
  return value.toLocaleString("en-IN", { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

function inr(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `₹${Math.round(value).toLocaleString("en-IN")}`;
}

export function ScannerPanel() {
  const [status, setStatus] = useState<ScannerStatus | null>(null);
  const [signals, setSignals] = useState<ScannerSignal[]>([]);
  const [heat, setHeat] = useState<HeatRow[]>([]);
  const [trades, setTrades] = useState<ScannerTrade[]>([]);
  const [rankings, setRankings] = useState<RankPair[]>([]);
  const [report, setReport] = useState<Record<string, unknown> | null>(null);
  const [settings, setSettings] = useState<ScannerSettings | null>(null);
  const [universes, setUniverses] = useState<Record<string, { label: string }>>({});
  const [selected, setSelected] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState("symbol");
  const [sortDir, setSortDir] = useState<1 | -1>(1);
  const [bottom, setBottom] = useState<"trades" | "report" | "rank">("trades");
  const [drawer, setDrawer] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const [st, sig, hm, tr, rep, univ, rank] = await Promise.all([
        fetchScannerStatus(signal),
        fetchScannerSignals(signal),
        fetchScannerHeatmap(signal),
        fetchScannerTrades(signal),
        fetchScannerReport(signal),
        fetchUniverses(signal),
        fetchRankings(signal),
      ]);
      setStatus(st);
      setSignals(sig.signals);
      setHeat(hm.rows);
      setTrades(tr.trades);
      setRankings(rank.pairs || []);
      setReport(rep.report);
      setUniverses(Object.fromEntries(Object.entries(univ.universes).map(([id, body]) => [id, { label: body.label }])));
      setError(null);
    } catch (err) {
      if (signal?.aborted) return;
      setError(err instanceof Error ? err.message : "scanner unavailable");
    }
  }, []);

  useEffect(() => {
    const ac = new AbortController();
    void load(ac.signal);
    const id = window.setInterval(() => void load(), 15000);
    return () => {
      ac.abort();
      window.clearInterval(id);
    };
  }, [load]);

  const rows = useMemo(() => sortRows(signals as unknown as Record<string, unknown>[], sortKey, sortDir) as unknown as ScannerSignal[], [signals, sortKey, sortDir]);
  const detail = rows.find((row) => row.id === selected) ?? null;

  const onSort = (key: string) => {
    if (key === sortKey) setSortDir((dir) => (dir === 1 ? -1 : 1));
    else {
      setSortKey(key);
      setSortDir(1);
    }
  };

  const onScan = async () => {
    setBusy(true);
    try {
      enableScannerSound();
      await scanNow();
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "scan failed");
    } finally {
      setBusy(false);
    }
  };

  const onUniverse = async (universe: string) => {
    await saveScannerSettings({ universe });
    await load();
  };

  return (
    <div className="flex h-full flex-col bg-[#070a0f] text-white" data-testid="scanner-pane">
      <header className="flex flex-wrap items-center gap-3 border-b border-white/10 px-3 py-2 text-xs">
        <span className={`rounded px-2 py-1 ${status?.trading_day ? "bg-emerald-500/15 text-emerald-200" : "bg-white/10 text-white/60"}`}>
          {status?.trading_day ? "Market day" : "Closed"}
        </span>
        <span className="text-white/70">14:30 in {status?.minutes_to_cutoff ?? "—"}m</span>
        <span className="text-white/70">15:15 in {status?.minutes_to_flat ?? "—"}m</span>
        <span className={status && status.net >= 0 ? "text-emerald-300" : "text-red-300"}>P&amp;L {inr(status?.net)}</span>
        <span className="text-white/70">
          Trades {status?.trades_used ?? 0}/{status?.max_trades ?? "—"}
        </span>
        <span className="text-white/70">
          Loss {inr(status?.loss_used)} / {inr(status?.daily_loss_limit)}
        </span>
        <label className="text-white/60">
          Universe{" "}
          <select
            className="rounded border border-white/15 bg-transparent px-2 py-1 text-white"
            value={status?.universe ?? "nifty50"}
            onChange={(event) => void onUniverse(event.target.value)}
            data-testid="scanner-universe"
          >
            {Object.entries(universes).map(([id, body]) => (
              <option key={id} value={id}>
                {body.label}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className="rounded border border-white/15 px-2 py-1 text-white/80"
          onClick={() => void onScan()}
          disabled={busy}
          data-testid="scanner-scan-now"
        >
          {busy ? "Scanning…" : "Scan now"}
        </button>
        <button type="button" className="rounded border border-white/15 px-2 py-1 text-white/80" onClick={() => setDrawer(true)} data-testid="scanner-settings">
          Settings
        </button>
        {status?.unmapped?.length ? <span className="text-amber-300">Unmapped: {status.unmapped.join(", ")}</span> : null}
        {error ? <span className="text-red-300">{error}</span> : null}
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 xl:grid-cols-[1fr_320px]">
        <div className="min-h-0 overflow-auto">
          <table className="w-full border-collapse text-left text-xs" data-testid="scanner-signals">
            <thead className="sticky top-0 bg-[#0d1118] text-white/50">
              <tr>
                {COLUMNS.map((col) => (
                  <th key={col.key} className="px-2 py-1 font-medium">
                    <button type="button" className="hover:text-white" onClick={() => onSort(col.key)}>
                      {col.label}
                      {sortKey === col.key ? (sortDir === 1 ? " ↑" : " ↓") : ""}
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr
                  key={row.id}
                  className={`cursor-pointer border-t border-white/5 hover:bg-white/5 ${selected === row.id ? "bg-white/10" : ""}`}
                  onClick={() => setSelected(row.id)}
                >
                  <td className="px-2 py-1">{row.symbol}</td>
                  <td className="px-2 py-1">{row.setup}</td>
                  <td className="px-2 py-1">{row.side}</td>
                  <td className="px-2 py-1">{row.status}</td>
                  <td className="px-2 py-1">{n(row.entry)}</td>
                  <td className="px-2 py-1">{n(row.stop)}</td>
                  <td className="px-2 py-1">{n(row.target)}</td>
                  <td className="px-2 py-1">{row.qty ?? "—"}</td>
                  <td className="px-2 py-1">{inr(row.rupee_risk)}</td>
                  <td className="px-2 py-1">{row.rr == null ? "—" : row.rr.toFixed(2)}</td>
                  <td className="px-2 py-1">{n(row.target_pct, 1)}</td>
                  <td className="px-2 py-1">{n(row.stop_pct, 1)}</td>
                  <td className="px-2 py-1">
                    {row.win_rate == null ? "—" : `${Math.round(row.win_rate * 100)}%`}
                    {row.history_source === "pooled" ? " pooled" : ""}
                  </td>
                  <td className={`px-2 py-1 ${verdictClass(row.verdict)}`}>{row.verdict ?? "—"}</td>
                  <td className={`px-2 py-1 ${decisionClass(row.decision)}`} title={(row.reasons || []).join(", ")}>
                    {row.decision ?? "—"}
                  </td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={COLUMNS.length} className="px-2 py-6 text-white/40">
                    No signals yet. The scanner runs at 09:30 and every 5 minutes until 14:30.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <aside className="min-h-0 overflow-auto border-l border-white/10 p-3 text-xs">
          {detail ? (
            <>
              <h2 className="mb-2 text-sm font-semibold">
                {detail.symbol} {detail.side} {detail.setup}
              </h2>
              <ScannerChart signal={detail} />
              <p className="mt-2 text-white/70">
                ₹ risk {inr(detail.rupee_risk)} · ₹ reward {inr(detail.rupee_reward)} · capital {inr(detail.capital)}
              </p>
              <p className="text-white/60">
                Support {n(detail.support)} · resistance {n(detail.resistance)} · {detail.news}
              </p>
              {detail.note ? <p className="text-white/50">{detail.note}</p> : null}
              {(detail.reasons || []).length > 0 && <p className="text-red-200">{detail.reasons?.join(" · ")}</p>}
              <h3 className="mt-3 font-medium">Time Machine</h3>
              <Histogram signal={detail} />
              <p className="text-white/60">
                Average result {inr(detail.avg_rupees)} · still open {n(detail.open_pct, 1)}%
              </p>
              <h3 className="mt-3 font-medium">
                Past fires ({detail.fires ?? 0}){detail.history_source === "pooled" ? " pooled" : ""}
              </h3>
              <ul className="max-h-40 space-y-1 overflow-auto text-white/70">
                {(detail.occurrences || []).slice(-12).map((item) => (
                  <li key={`${item.day}-${item.reason}`}>
                    {item.day} {item.side} {item.reason} {item.r.toFixed(2)}R
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <p className="text-white/40">Select a signal to see the plan, the chart, and the Time Machine.</p>
          )}
          <h3 className="mt-4 font-medium">Heatmap</h3>
          <div className="mt-2 grid grid-cols-2 gap-1" data-testid="scanner-heatmap">
            {heat.map((row) => (
              <div
                key={row.symbol}
                className={`rounded px-2 py-1 ${row.change_pct != null && row.change_pct >= 0 ? "bg-emerald-500/15" : "bg-red-500/15"}`}
                title={`RVOL ${row.rvol?.toFixed(2) ?? "—"} · vs Nifty ${row.vs_nifty?.toFixed(2) ?? "—"}`}
              >
                <div className="flex justify-between">
                  <span>{row.symbol}</span>
                  <span>{row.change_pct == null ? "—" : `${row.change_pct.toFixed(1)}%`}</span>
                </div>
                <div className="text-[10px] text-white/50">{row.sector}</div>
              </div>
            ))}
          </div>
        </aside>
      </div>

      <section className="h-40 shrink-0 overflow-auto border-t border-white/10">
        <div className="flex gap-2 px-3 pt-2 text-xs">
          <button type="button" className={bottom === "trades" ? "text-white" : "text-white/45"} onClick={() => setBottom("trades")}>
            Slot 8 trades
          </button>
          <button type="button" className={bottom === "report" ? "text-white" : "text-white/45"} onClick={() => setBottom("report")}>
            End-of-day report
          </button>
          <button type="button" className={bottom === "rank" ? "text-white" : "text-white/45"} onClick={() => setBottom("rank")}>
            Rankings
          </button>
        </div>
        {bottom === "rank" ? (
          <table className="w-full text-left text-xs" data-testid="scanner-rankings">
            <thead className="text-white/45">
              <tr>
                <th className="px-2 py-1 font-medium">Stock</th>
                <th className="px-2 py-1 font-medium">Setup</th>
                <th className="px-2 py-1 font-medium">Trades</th>
                <th className="px-2 py-1 font-medium">Win %</th>
                <th className="px-2 py-1 font-medium">Avg R</th>
                <th className="px-2 py-1 font-medium">PF</th>
                <th className="px-2 py-1 font-medium">Max DD</th>
                <th className="px-2 py-1 font-medium">Net</th>
              </tr>
            </thead>
            <tbody>
              {rankings.map((pair) => (
                <tr key={`${pair.symbol}-${pair.setup}`} className="border-t border-white/5">
                  <td className="px-2 py-1">{pair.symbol}</td>
                  <td className="px-2 py-1">{pair.setup}</td>
                  <td className="px-2 py-1">{pair.trades}</td>
                  <td className="px-2 py-1">{pair.win_rate == null ? "—" : `${Math.round(pair.win_rate * 100)}%`}</td>
                  <td className="px-2 py-1">{pair.avg_r == null ? "—" : pair.avg_r.toFixed(2)}</td>
                  <td className="px-2 py-1">{pair.profit_factor == null ? "—" : pair.profit_factor.toFixed(2)}</td>
                  <td className="px-2 py-1">{inr(pair.max_drawdown)}</td>
                  <td className="px-2 py-1">{inr(pair.net)}</td>
                </tr>
              ))}
              {rankings.length === 0 && (
                <tr>
                  <td className="px-2 py-3 text-white/40">No ranking yet. Saturday’s job writes it, and the next backend start refreshes a stale file.</td>
                </tr>
              )}
            </tbody>
          </table>
        ) : bottom === "trades" ? (
          <table className="w-full text-left text-xs">
            <tbody>
              {trades.map((trade, i) => (
                <tr key={`${trade.symbol}-${i}`} className="border-t border-white/5">
                  <td className="px-2 py-1">{trade.symbol}</td>
                  <td className="px-2 py-1">{trade.setup}</td>
                  <td className="px-2 py-1">{trade.side}</td>
                  <td className="px-2 py-1">{n(trade.entry)}</td>
                  <td className="px-2 py-1">{n(trade.exit)}</td>
                  <td className="px-2 py-1">{trade.r.toFixed(2)}R</td>
                  <td className="px-2 py-1">{inr(trade.net)}</td>
                  <td className="px-2 py-1">{trade.exit_reason}</td>
                </tr>
              ))}
              {trades.length === 0 && (
                <tr>
                  <td className="px-2 py-3 text-white/40">No slot 8 trades today.</td>
                </tr>
              )}
            </tbody>
          </table>
        ) : (
          <pre className="whitespace-pre-wrap px-3 py-2 text-[11px] text-white/70">{report ? JSON.stringify(report, null, 2) : "Report is written at 15:35."}</pre>
        )}
      </section>
      {drawer && (
        <SettingsDrawer
          onClose={() => setDrawer(false)}
          initial={settings}
          onLoad={async () => setSettings(await fetchScannerSettings())}
          onSave={async (next) => {
            const saved = await saveScannerSettings(next);
            setSettings(saved);
            setDrawer(false);
            await load();
          }}
        />
      )}
    </div>
  );
}

function Histogram({ signal }: { signal: ScannerSignal }) {
  const hist = signal.histogram;
  if (!hist) return <p className="text-white/40">Not run yet.</p>;
  const total = Math.max(1, hist.target + hist.stop + hist.open);
  const bars = [
    { label: "Target", n: hist.target, cls: "bg-emerald-400" },
    { label: "Stop", n: hist.stop, cls: "bg-red-400" },
    { label: "Open", n: hist.open, cls: "bg-white/30" },
  ];
  return (
    <div className="mt-1 space-y-1" data-testid="scanner-histogram">
      {bars.map((bar) => (
        <div key={bar.label} className="flex items-center gap-2">
          <span className="w-12 text-white/50">{bar.label}</span>
          <div className="h-2 flex-1 rounded bg-white/5">
            <div className={`h-2 rounded ${bar.cls}`} style={{ width: `${(bar.n / total) * 100}%` }} />
          </div>
          <span className="w-10 text-right text-white/70">{bar.n}</span>
        </div>
      ))}
    </div>
  );
}

function SettingsDrawer({
  onClose,
  onSave,
  onLoad,
  initial,
}: {
  onClose: () => void;
  onSave: (next: Partial<ScannerSettings>) => Promise<void>;
  onLoad: () => Promise<void>;
  initial: ScannerSettings | null;
}) {
  const [draft, setDraft] = useState<ScannerSettings | null>(initial);
  useEffect(() => {
    if (!draft) void onLoad().then(() => fetchScannerSettings().then(setDraft));
  }, [draft, onLoad]);
  if (!draft) return null;
  const set = (patch: Partial<ScannerSettings>) => setDraft({ ...draft, ...patch });
  return (
    <div className="absolute inset-y-0 right-0 z-30 w-80 overflow-auto border-l border-white/10 bg-[#0d1118] p-3 text-xs" data-testid="scanner-settings-drawer">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-semibold">Scanner settings</h2>
        <button type="button" onClick={onClose} className="text-white/50">
          Close
        </button>
      </div>
      <Field label="Account size ₹" value={draft.account_size} onChange={(v) => set({ account_size: v })} />
      <Field label="Max loss per trade ₹" value={draft.max_loss_per_trade} onChange={(v) => set({ max_loss_per_trade: v })} />
      <Field label="Daily loss limit ₹" value={draft.daily_loss_limit} onChange={(v) => set({ daily_loss_limit: v })} />
      <Field label="Max trades" value={draft.max_trades_per_day} onChange={(v) => set({ max_trades_per_day: v })} />
      <Field label="Max open positions" value={draft.max_open_positions} onChange={(v) => set({ max_open_positions: v })} />
      <Field label="Max capital per trade ₹" value={draft.max_capital_per_trade} onChange={(v) => set({ max_capital_per_trade: v })} />
      <Field label="Min reward:risk" value={draft.min_rr} onChange={(v) => set({ min_rr: v })} />
      <Field label="Time Machine paths" value={draft.tm_paths} onChange={(v) => set({ tm_paths: v })} />
      <Field label="GOOD edge (points)" value={draft.tm_edge_points} onChange={(v) => set({ tm_edge_points: v })} />
      <Field label="GOOD min history win rate" value={draft.tm_min_win_rate} onChange={(v) => set({ tm_min_win_rate: v })} />
      <Field label="GOOD min past fires" value={draft.tm_min_fires} onChange={(v) => set({ tm_min_fires: v })} />
      <label className="mt-2 block text-white/60">
        Entry
        <select className="mt-1 w-full rounded border border-white/15 bg-transparent px-2 py-1" value={draft.entry_mode} onChange={(e) => set({ entry_mode: e.target.value })}>
          <option value="trigger_close">Trigger close</option>
          <option value="next_open">Next bar open</option>
        </select>
      </label>
      <label className="mt-2 block text-white/60">
        ORB stop
        <select className="mt-1 w-full rounded border border-white/15 bg-transparent px-2 py-1" value={draft.orb_stop} onChange={(e) => set({ orb_stop: e.target.value })}>
          <option value="midpoint">Range midpoint</option>
          <option value="opposite">Opposite side</option>
        </select>
      </label>
      <label className="mt-2 block text-white/60">
        Target
        <select className="mt-1 w-full rounded border border-white/15 bg-transparent px-2 py-1" value={draft.target_mode} onChange={(e) => set({ target_mode: e.target.value })}>
          <option value="2r">2R</option>
          <option value="3r">3R</option>
          <option value="next_level">Next swing</option>
        </select>
      </label>
      <label className="mt-2 flex items-center gap-2">
        <input type="checkbox" checked={draft.trail_breakeven} onChange={(e) => set({ trail_breakeven: e.target.checked })} />
        Move stop to break-even at 1R
      </label>
      <label className="mt-2 flex items-center gap-2">
        <input type="checkbox" checked={draft.only_proven} onChange={(e) => set({ only_proven: e.target.checked })} />
        Only proven stock + setup pairs
      </label>
      <label className="mt-2 flex items-center gap-2">
        <input
          type="checkbox"
          checked={draft.auto_trade}
          disabled={!draft.replay_ready}
          onChange={(e) => set({ auto_trade: e.target.checked })}
        />
        Slot 8 takes PASS signals
      </label>
      {!draft.replay_ready && draft.replay_reason ? <p className="mt-1 text-amber-200">{draft.replay_reason}</p> : null}
      <div className="mt-3 space-y-1">
        {Object.entries(draft.setups).map(([name, on]) => (
          <label key={name} className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={on}
              onChange={(e) => set({ setups: { ...draft.setups, [name]: e.target.checked } })}
            />
            {name}
          </label>
        ))}
      </div>
      <button type="button" className="mt-3 rounded bg-white/10 px-3 py-1" onClick={() => void onSave(draft)}>
        Save
      </button>
    </div>
  );
}

function Field({ label, value, onChange }: { label: string; value: number; onChange: (v: number) => void }) {
  return (
    <label className="mt-2 block text-white/60">
      {label}
      <input
        className="mt-1 w-full rounded border border-white/15 bg-transparent px-2 py-1 text-white"
        type="number"
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </label>
  );
}
