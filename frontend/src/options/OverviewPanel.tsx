/** Options tab Overview — 8:50 bias headline, summary, pre-market checks, component bars. */

import { useEffect, useRef, useState } from "react";

import { fetchMarketView, type MarketFactor, type MarketView } from "../api/marketView";
import type { OptionsBias, OptionsChainResponse, OptionsOverview } from "../api/options";
import { formatSpot, formatStrike } from "./format";

function isBeforeOpenIst(): boolean {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Kolkata",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(new Date());
  const hour = Number(parts.find((p) => p.type === "hour")?.value ?? 99);
  const minute = Number(parts.find((p) => p.type === "minute")?.value ?? 99);
  return hour * 60 + minute < 9 * 60 + 15;
}

function biasColor(label: string): string {
  if (label.includes("Strong Bullish")) return "text-emerald-300";
  if (label.includes("Mildly Bullish")) return "text-emerald-200/90";
  if (label.includes("Strong Bearish")) return "text-red-300";
  if (label.includes("Mildly Bearish")) return "text-red-200/90";
  return "text-white/70";
}

function factorTone(score: number | null): string {
  if (score == null) return "text-white/35";
  if (score > 0) return "text-emerald-300";
  if (score < 0) return "text-red-300";
  return "text-white/55";
}

function fmtDist(d: number | null | undefined): string {
  if (d == null) return "—";
  return `${d > 0 ? "+" : ""}${formatStrike(d)}`;
}

function fmtDelta(d: number | null | undefined, digits = 0): string {
  if (d == null) return "—";
  const n = digits === 0 ? Math.round(d) : Number(d.toFixed(digits));
  return `${n > 0 ? "+" : ""}${n}`;
}

/** One entry when R1/S1 shares the pivot strike: "R1 / PIVOT 25000 (+14)". */
function formatOverviewLevels(lv: OptionsOverview["levels"]): string {
  const mergeR1 = lv.pivot_label === "R1 / PIVOT" && lv.pivot != null;
  const mergeS1 = lv.pivot_label === "S1 / PIVOT" && lv.pivot != null;
  const parts: string[] = [];
  if (!mergeR1) {
    parts.push(`R1 ${lv.r1 != null ? formatStrike(lv.r1) : "—"} (${fmtDist(lv.r1_dist)})`);
  }
  if (lv.pivot != null) {
    parts.push(`${lv.pivot_label ?? "PIVOT"} ${formatStrike(lv.pivot)} (${fmtDist(lv.pivot_dist)})`);
  } else {
    parts.push("PIVOT —");
  }
  if (!mergeS1) {
    parts.push(`S1 ${lv.s1 != null ? formatStrike(lv.s1) : "—"} (${fmtDist(lv.s1_dist)})`);
  }
  parts.push(
    `Max Pain ${lv.max_pain != null ? formatStrike(lv.max_pain) : "—"} (${fmtDist(lv.max_pain_dist)})`,
  );
  return parts.join(" · ");
}

function ComponentBars({ bias }: { bias: OptionsBias }) {
  return (
    <div className="mt-2 space-y-1" data-testid="options-bias-components">
      {bias.components.map((c) => {
        const pct = Math.round(Math.abs(c.contribution) * 100);
        const positive = c.contribution >= 0;
        return (
          <div key={c.key} className="flex items-center gap-2 text-[9px]">
            <span className="w-28 shrink-0 truncate text-white/45" title={c.label}>
              {c.label}
            </span>
            <span className="w-8 shrink-0 font-mono text-white/35">{Math.round(c.weight * 100)}%</span>
            <div className="relative h-1.5 flex-1 overflow-hidden rounded-sm bg-white/10">
              <div
                className={`absolute top-0 h-full ${positive ? "left-1/2 bg-emerald-400/70" : "right-1/2 bg-red-400/70"}`}
                style={{ width: `${Math.min(50, pct / 2)}%` }}
              />
            </div>
            <span className={`w-8 text-right font-mono ${positive ? "text-emerald-300/80" : "text-red-300/80"}`}>
              {c.score >= 0 ? "+" : ""}
              {c.score.toFixed(2)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function PremarketChecks({ view }: { view: MarketView | null }) {
  const factors: MarketFactor[] = view?.factors ?? [];
  return (
    <div className="mt-2" data-testid="options-premarket-checks">
      <div className="mb-1 text-[10px] uppercase tracking-wide text-white/40">Pre-market checks</div>
      {!view ? (
        <p className="text-[10px] text-white/35">Pre-market view unavailable</p>
      ) : (
        <ul className="space-y-0.5">
          {factors.map((f) => (
            <li key={f.key} className="flex justify-between gap-2 text-[10px]">
              <span className="text-white/50">{f.label}</span>
              <span className={`font-mono ${factorTone(f.score)}`} title={f.reason}>
                {f.available ? f.value : "—"}
                {f.score != null ? ` (${f.score > 0 ? "+" : ""}${f.score})` : ""}
              </span>
            </li>
          ))}
        </ul>
      )}
      <p className="mt-1 text-[10px] text-white/35">GIFT Nifty: not connected</p>
    </div>
  );
}

export function OverviewPanel({ data }: { data: OptionsChainResponse }) {
  const bias = data.bias;
  const overview: OptionsOverview | null | undefined = data.overview;
  const ref = useRef<HTMLDivElement>(null);
  const [mv, setMv] = useState<MarketView | null>(null);

  useEffect(() => {
    const ac = new AbortController();
    void fetchMarketView(false, ac.signal)
      .then((v) => {
        if (!ac.signal.aborted) setMv(v);
      })
      .catch(() => {
        if (!ac.signal.aborted) setMv(null);
      });
    return () => ac.abort();
  }, [data.as_of]);

  useEffect(() => {
    if (!isBeforeOpenIst()) return;
    ref.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  if (!bias || !overview) {
    return (
      <div
        ref={ref}
        className="rounded border border-dashed border-white/10 px-3 py-3 text-[11px] text-white/30"
        data-testid="options-overview"
      >
        Overview — bias unavailable
      </div>
    );
  }

  const lv = overview.levels;
  const em = overview.expected_range;
  const mp = em.market_priced;
  const ch = bias.changed_since_yesterday;

  return (
    <section
      ref={ref}
      className="shrink-0 border-b border-white/10 bg-[#0b0f14] px-3 py-3"
      data-testid="options-overview"
    >
      <div className="text-[10px] uppercase tracking-wide text-white/40">Overview</div>
      <h2 className={`mt-0.5 text-base font-semibold ${biasColor(bias.label)}`} data-testid="options-bias-headline">
        {overview.headline}
      </h2>

      <div className="mt-2 space-y-1 text-[11px] text-white/65">
        <p data-testid="overview-levels">Levels: {formatOverviewLevels(lv)}</p>
        {lv.pivot_read ? (
          <p className="text-[10px] text-sky-100/60" data-testid="overview-pivot-read">
            {lv.pivot_label ?? "PIVOT"}: {lv.pivot_read}
          </p>
        ) : null}
        <p data-testid="overview-em">
          Market-priced (ATM straddle){" "}
          {mp?.move != null ? `±${formatStrike(mp.move)} → ${formatStrike(mp.lower)} / ${formatStrike(mp.upper)}` : "—"}
          {overview.expected_range.range_move != null ? (
            <> · implied range ±{formatStrike(overview.expected_range.range_move)}</>
          ) : null}
        </p>
        <p data-testid="overview-picker">Strike picker: {overview.picker}</p>
      </div>

      {ch ? (
        <div className="mt-2 text-[10px] text-white/45" data-testid="overview-changed">
          <div className="uppercase tracking-wide text-white/35">What changed since yesterday</div>
          <div className="mt-0.5 font-mono">
            R1 {fmtDelta(ch.r1)} · S1 {fmtDelta(ch.s1)} · Pivot {fmtDelta(ch.pivot)} · Max Pain{" "}
            {fmtDelta(ch.max_pain)} · PCR {fmtDelta(ch.pcr_full, 2)} · ATM IV {fmtDelta(ch.atm_iv, 1)}
            {ch.prev_spot != null ? ` · prev spot ${formatSpot(ch.prev_spot)}` : ""}
          </div>
        </div>
      ) : (
        <p className="mt-2 text-[10px] text-white/30">No prior EOD snapshot to compare</p>
      )}

      <div className="mt-2 grid gap-3 md:grid-cols-2">
        <PremarketChecks view={mv} />
        <div>
          <div className="mb-1 text-[10px] uppercase tracking-wide text-white/40">Why this score</div>
          <ComponentBars bias={bias} />
        </div>
      </div>
    </section>
  );
}
