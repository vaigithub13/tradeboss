import { useEffect, useRef, useState } from "react";

import {
  INSTRUMENT_KINDS,
  searchInstruments,
  type InstrumentHit,
  type InstrumentKind,
  type InstrumentSearchResponse,
} from "../api/client";
import { Popover } from "../components/Popover";
import { useChartStore } from "../store/chartStore";
import { useUpstoxStore } from "../store/upstoxStore";
import { dataFlag, hitDetail, jobText, tokenBlocksFetching } from "./upstoxUi";

const KIND_LABEL: Record<InstrumentKind, string> = {
  index: "Indices",
  equity: "Stocks",
  future: "Futures",
  option: "Options",
};
const SEARCH_DEBOUNCE_MS = 200;

function Results({ close }: { close: () => void }) {
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<InstrumentKind | null>(null);
  const [res, setRes] = useState<InstrumentSearchResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const token = useUpstoxStore((s) => s.token);
  const jobs = useUpstoxStore((s) => s.jobs);
  const openInstrument = useUpstoxStore((s) => s.openInstrument);
  const loadMoreHistory = useUpstoxStore((s) => s.loadMoreHistory);
  const syncLatest = useUpstoxStore((s) => s.syncLatest);
  const current = useChartStore((s) => s.symbol);
  const blocked = tokenBlocksFetching(token);
  const inputRef = useRef<HTMLInputElement>(null);
  const symbols = useChartStore((s) => s.symbols);

  useEffect(() => inputRef.current?.focus(), []);

  useEffect(() => {
    const controller = new AbortController();
    const id = window.setTimeout(() => {
      searchInstruments(query, kind, 40, controller.signal)
        .then((r) => {
          setRes(r);
          setError(null);
        })
        .catch((e: unknown) => {
          if (e instanceof DOMException && e.name === "AbortError") return;
          setError(e instanceof Error ? e.message : String(e));
        });
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      controller.abort();
      window.clearTimeout(id);
    };
  // `symbols` changes when a sync finishes: re-run so the 1m / fetch badges are up to date
  }, [query, kind, symbols]);

  const choose = (hit: InstrumentHit): void => {
    if (!hit.has_data && blocked) return;
    void openInstrument(hit);
    if (hit.has_data) close(); // stored: switches at once; otherwise keep the panel open to show progress
  };

  const info = symbols.find((s) => s.symbol === current);
  const currentKey = info?.instrument_key ?? null;
  const currentJob = currentKey ? jobs[currentKey] : undefined;

  return (
    <div className="flex flex-col gap-2" data-testid="symbol-panel">
      <input
        ref={inputRef}
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && res?.items[0]) choose(res.items[0]);
        }}
        placeholder="Search: nifty, reliance, nifty 24000 ce, fut…"
        aria-label="Search symbol"
        className="w-full rounded border border-white/15 bg-[#0b0e14] px-2 py-1.5 text-xs text-white placeholder:text-white/30"
      />
      <div className="flex flex-wrap gap-1" role="group" aria-label="Instrument type">
        {[null, ...INSTRUMENT_KINDS].map((k) => (
          <button
            key={k ?? "all"}
            type="button"
            aria-pressed={kind === k}
            onClick={() => setKind(k)}
            className={`rounded px-2 py-0.5 text-[11px] ${kind === k ? "bg-sky-600 text-white" : "bg-white/5 text-white/60 hover:bg-white/10"}`}
          >
            {k ? KIND_LABEL[k] : "All"}
          </button>
        ))}
      </div>

      {blocked && token && (
        <div className="rounded bg-red-900/40 px-2 py-1 text-[11px] text-red-200" data-testid="token-warning">
          {token.message} Stored symbols still open; fetching new history is disabled.
        </div>
      )}
      {error && <div className="text-[11px] text-red-300">{error}</div>}
      {res?.message && <div className="text-[11px] text-amber-300">{res.message}</div>}

      <ul className="max-h-72 overflow-auto" data-testid="symbol-results">
        {res?.items.map((hit) => {
          const flag = dataFlag(hit);
          const job = jobs[hit.instrument_key];
          const disabled = flag === "fetch" && blocked;
          return (
            <li key={hit.instrument_key}>
              <button
                type="button"
                disabled={disabled}
                onClick={() => choose(hit)}
                title={disabled ? "Data token is not valid: cannot fetch history for this symbol yet" : undefined}
                className={`flex w-full items-center justify-between gap-2 rounded px-2 py-1 text-left text-xs hover:bg-white/5 ${disabled ? "cursor-not-allowed opacity-40" : ""} ${hit.symbol_id === current ? "bg-white/10" : ""}`}
                data-testid="symbol-row"
              >
                <span className="min-w-0">
                  <span className="font-semibold text-white">{hit.symbol}</span>
                  <span className="block truncate text-[11px] text-white/45">{hitDetail(hit)}</span>
                  {job && job.status !== "done" && (
                    <span className={`block text-[11px] ${job.status === "error" ? "text-red-300" : "text-sky-300"}`}>{jobText(job)}</span>
                  )}
                </span>
                <span
                  className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] ${flag === "1m" ? "bg-emerald-500/20 text-emerald-300" : flag === "partial" ? "bg-yellow-500/20 text-yellow-300" : "bg-white/10 text-white/50"}`}
                >
                  {flag === "1m" ? "1m" : flag === "partial" ? "no 1m" : "fetch"}
                </span>
              </button>
            </li>
          );
        })}
        {res && res.items.length === 0 && !res.message && <li className="px-2 py-2 text-xs text-white/40">No match</li>}
      </ul>

      {currentKey && (
        <div className="flex flex-col gap-1 border-t border-white/10 pt-2" data-testid="symbol-actions">
          <div className="flex gap-1">
            <button
              type="button"
              disabled={blocked || currentJob?.status === "running"}
              onClick={() => void syncLatest(currentKey)}
              className="rounded bg-white/5 px-2 py-1 text-[11px] text-white/80 hover:bg-white/10 disabled:opacity-40"
            >
              Sync latest 1m
            </button>
            <button
              type="button"
              disabled={blocked || currentJob?.status === "running"}
              onClick={() => void loadMoreHistory(currentKey)}
              className="rounded bg-white/5 px-2 py-1 text-[11px] text-white/80 hover:bg-white/10 disabled:opacity-40"
            >
              Load 3 more months
            </button>
          </div>
          {currentJob && (
            <span className={`text-[11px] ${currentJob.status === "error" ? "text-red-300" : "text-white/50"}`}>
              {info?.display_name}: {jobText(currentJob)}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

/** Header control: current symbol + a searchable list of indices, NSE stocks, Nifty futures / options. */
export function SymbolSelector() {
  const symbols = useChartStore((s) => s.symbols);
  const symbol = useChartStore((s) => s.symbol);
  const info = symbols.find((s) => s.symbol === symbol);
  return (
    <Popover label={`${info?.display_name ?? "Symbol"} ▾`} title="Change symbol" panelClassName="w-[26rem]">
      {(close) => <Results close={close} />}
    </Popover>
  );
}
