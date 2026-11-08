import { useEffect, useMemo, useRef, useState } from "react";

import type {
  BuildupLabel,
  HoldMode,
  OptionsChainResponse,
  OptionsLeg,
  OptionsLevel,
  OptionsRow,
  PcrValue,
  StrikeDirection,
  StrikePickerSide,
} from "../api/options";
import { useOptionsStore } from "../store/optionsStore";
import { formatProb, repricePickerSide } from "./estimate";
import {
  chgClass,
  formatChg,
  formatIstClock,
  formatIv,
  formatOiChg,
  formatOiLakhs,
  formatPct,
  formatSpot,
  formatStrike,
} from "./format";
import { buildKeyLevelsLadder } from "./levels";
import { OiBarChart } from "./OiBarChart";
import { TrendsPanel } from "./TrendsPanel";

const BUILDUP_SHORT: Record<BuildupLabel, string> = {
  "Long Build-up": "LBu",
  "Short Build-up": "SBu",
  "Short Covering": "SC",
  "Long Unwinding": "LU",
  Neutral: "·",
};

const POLL_MS = 60_000;

type GreekCol = "delta" | "gamma" | "theta" | "vega";

const GREEK_DEFAULTS: Record<GreekCol, boolean> = {
  delta: true,
  gamma: false,
  theta: true,
  vega: false,
};

function Card({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="min-w-[7.5rem] rounded border border-white/10 bg-white/[0.03] px-3 py-2">
      <div className="text-[10px] uppercase tracking-wide text-white/40">{label}</div>
      <div className="font-mono text-sm text-white/90">{value}</div>
      {sub ? <div className="text-[10px] text-white/35">{sub}</div> : null}
    </div>
  );
}

function OiBar({ oi, max }: { oi: number; max: number }) {
  const pct = max > 0 ? Math.min(100, (oi / max) * 100) : 0;
  return (
    <div className="mt-0.5 h-1 w-full overflow-hidden rounded-sm bg-white/10">
      <div className="h-full bg-sky-400/70" style={{ width: `${pct}%` }} />
    </div>
  );
}

function greekCell(value: number | null | undefined, digits = 3): string {
  if (value == null || Number.isNaN(value)) return "—";
  return value.toFixed(digits);
}

function LegCells({
  leg,
  maxOi,
  mirror,
  itm,
  greeks,
}: {
  leg: OptionsLeg;
  maxOi: number;
  mirror?: boolean;
  itm?: boolean;
  greeks: Record<GreekCol, boolean>;
}) {
  const shade = itm ? (mirror ? "bg-red-500/[0.06]" : "bg-emerald-500/[0.06]") : "";
  const oi = (
    <td key="oi" className={`px-1.5 py-1 text-right font-mono ${shade}`}>
      {formatOiLakhs(leg.oi)}
      <OiBar oi={leg.oi} max={maxOi} />
    </td>
  );
  const chgOi = (
    <td key="chgOi" className={`px-1.5 py-1 text-right font-mono ${shade} ${chgClass(leg.oi_chg)}`}>
      {formatOiChg(leg.oi_chg, leg.oi_chg_pct)}
    </td>
  );
  const vol = (
    <td key="vol" className={`px-1.5 py-1 text-right font-mono text-white/70 ${shade}`}>
      {formatStrike(leg.volume)}
    </td>
  );
  const iv = (
    <td key="iv" className={`px-1.5 py-1 text-right font-mono text-white/70 ${shade}`}>
      {formatIv(leg.iv)}
    </td>
  );
  const ltp = (
    <td key="ltp" className={`px-1.5 py-1 text-right font-mono ${shade}`}>
      {formatSpot(leg.ltp)}
    </td>
  );
  const buildupColor =
    leg.buildup_nifty === "Bullish"
      ? "text-emerald-300"
      : leg.buildup_nifty === "Bearish"
        ? "text-red-300"
        : "text-white/35";
  const chg = (
    <td key="chg" className={`px-1.5 py-1 text-right font-mono ${shade} ${chgClass(leg.price_chg)}`}>
      {formatChg(leg.price_chg)}
      {leg.buildup ? (
        <div className={`text-[8px] leading-tight ${buildupColor}`} title={`${leg.buildup}${leg.buildup_nifty ? ` · ${leg.buildup_nifty}` : ""}`}>
          {BUILDUP_SHORT[leg.buildup]}
        </div>
      ) : null}
    </td>
  );
  const bid = (
    <td key="bid" className={`px-1.5 py-1 text-right font-mono text-white/70 ${shade}`}>
      {formatSpot(leg.bid)}
    </td>
  );
  const ask = (
    <td key="ask" className={`px-1.5 py-1 text-right font-mono text-white/70 ${shade}`}>
      {formatSpot(leg.ask)}
    </td>
  );
  const spread = (
    <td key="spread" className={`px-1.5 py-1 text-right font-mono text-white/55 ${shade}`}>
      {formatPct(leg.spread_pct)}
    </td>
  );
  const delta = greeks.delta ? (
    <td key="delta" className={`px-1.5 py-1 text-right font-mono text-white/70 ${shade}`}>
      {greekCell(leg.delta, 3)}
    </td>
  ) : null;
  const gamma = greeks.gamma ? (
    <td key="gamma" className={`px-1.5 py-1 text-right font-mono text-white/70 ${shade}`}>
      {greekCell(leg.gamma, 4)}
    </td>
  ) : null;
  const theta = greeks.theta ? (
    <td key="theta" className={`px-1.5 py-1 text-right font-mono text-white/70 ${shade}`}>
      {greekCell(leg.theta, 2)}
    </td>
  ) : null;
  const vega = greeks.vega ? (
    <td key="vega" className={`px-1.5 py-1 text-right font-mono text-white/70 ${shade}`}>
      {greekCell(leg.vega, 2)}
    </td>
  ) : null;
  const cells = [oi, chgOi, vol, iv, ltp, chg, bid, ask, spread, delta, gamma, theta, vega].filter(
    Boolean,
  );
  return <>{mirror ? cells.reverse() : cells}</>;
}

function levelByTag(data: OptionsChainResponse, tag: string): OptionsLevel | undefined {
  return [...(data.resistance ?? []), ...(data.support ?? [])].find((l) => l.tag === tag);
}

function ProbLine({ pClose, pTouch }: { pClose?: number | null; pTouch?: number | null }) {
  if (pClose == null && pTouch == null) return null;
  return (
    <div className="mt-0.5 text-[10px] text-white/40" title="P(close beyond) · P(touch) — lognormal, not Upstox pop">
      close {formatProb(pClose)} · touch {formatProb(pTouch)}
    </div>
  );
}

function EmBandLine({ data }: { data: OptionsChainResponse }) {
  const em = data.expected_move;
  if (!em) return null;
  return (
    <li
      className="rounded border border-dashed border-amber-400/30 bg-amber-400/5 px-2 py-1.5"
      data-testid="options-em-ladder"
    >
      <div className="text-[10px] uppercase tracking-wide text-amber-200/70">Expected move</div>
      <div className="font-mono text-[11px] text-amber-100">
        {formatStrike(em.lower)} – {formatStrike(em.upper)}
        <span className="ml-1 text-white/40">±{formatStrike(em.move)}</span>
      </div>
    </li>
  );
}

function KeyLevels({ data }: { data: OptionsChainResponse }) {
  const spot = data.spot ?? 0;
  const ladder = buildKeyLevelsLadder(data);
  return (
    <aside className="flex w-72 shrink-0 flex-col gap-2 overflow-y-auto border-l border-white/10 bg-[#0b0f14] p-3 text-xs">
      <h2 className="text-sm font-semibold text-white">Key Levels</h2>
      <ul className="flex flex-col gap-1">
        <EmBandLine data={data} />
        {ladder.map((item) => {
          if (item.kind === "SPOT") {
            return (
              <li
                key={item.key}
                className="rounded border border-amber-400/40 bg-amber-400/10 px-2 py-1.5 font-mono text-amber-100"
              >
                <div className="flex justify-between">
                  <span>Spot</span>
                  <span>{formatSpot(data.spot)}</span>
                </div>
              </li>
            );
          }
          if (item.kind === "PIVOT" && item.pivot) {
            const p = item.pivot;
            const dist = p.strike - spot;
            return (
              <li
                key="PIVOT"
                className="rounded border border-sky-400/40 bg-sky-400/10 px-2 py-1.5"
                data-testid="options-pivot"
              >
                <div className="flex justify-between font-mono text-sky-100">
                  <span>PIVOT</span>
                  <span>{formatStrike(p.strike)}</span>
                </div>
                <div className="mt-0.5 flex justify-between text-[10px] text-white/45">
                  <span>
                    C {formatOiLakhs(p.call_oi)} · P {formatOiLakhs(p.put_oi)}
                  </span>
                  <span>
                    {dist > 0 ? "+" : ""}
                    {formatStrike(dist)} pts
                  </span>
                </div>
                <ProbLine pClose={p.p_close} pTouch={p.p_touch} />
                <p className="mt-1 text-[10px] leading-snug text-sky-100/70">{p.read}</p>
              </li>
            );
          }
          if (item.kind === "MAX_PAIN" && item.maxPain) {
            const mp = item.maxPain;
            const dist = mp.distance ?? mp.strike - spot;
            return (
              <li
                key="MAX PAIN"
                className="rounded border border-violet-400/40 bg-violet-400/10 px-2 py-1.5"
                data-testid="options-max-pain"
              >
                <div className="flex justify-between font-mono text-violet-100">
                  <span>MAX PAIN</span>
                  <span>{formatStrike(mp.strike)}</span>
                </div>
                <div className="mt-0.5 text-[10px] text-white/45">
                  {dist > 0 ? "+" : ""}
                  {formatStrike(dist)} pts from spot
                </div>
                <ProbLine pClose={mp.p_close} pTouch={mp.p_touch} />
              </li>
            );
          }
          const lvl = item.lvl;
          const dist = lvl ? lvl.strike - spot : null;
          return (
            <li key={item.key} className="rounded border border-white/10 px-2 py-1.5">
              <div className="flex justify-between font-mono">
                <span className={item.kind === "R" ? "text-red-300" : "text-emerald-300"}>
                  {lvl?.tag ?? item.key}
                  {lvl?.edge ? <span className="ml-1 text-[9px] text-amber-300/80">edge</span> : null}
                </span>
                <span>{lvl ? formatStrike(lvl.strike) : "—"}</span>
              </div>
              <div className="mt-0.5 flex justify-between text-[10px] text-white/40">
                <span>
                  {lvl ? formatOiLakhs(lvl.oi) : ""}
                  {lvl?.strength ? (
                    <span
                      className={`ml-1 ${
                        lvl.strength === "Strong"
                          ? "text-emerald-300/80"
                          : lvl.strength === "Medium"
                            ? "text-amber-300/70"
                            : "text-white/35"
                      }`}
                    >
                      {lvl.strength}
                    </span>
                  ) : null}
                </span>
                <span>{dist == null ? "" : `${dist > 0 ? "+" : ""}${formatStrike(dist)} pts`}</span>
              </div>
              <ProbLine pClose={lvl?.p_close} pTouch={lvl?.p_touch} />
            </li>
          );
        })}
      </ul>

      <div className="rounded border border-white/10 px-2 py-2">
        <div className="mb-1 text-[10px] uppercase tracking-wide text-white/40">Major walls</div>
        <MajorWallLine kind="Call" wall={data.major_walls?.call} spot={spot} />
        <MajorWallLine kind="Put" wall={data.major_walls?.put} spot={spot} />
      </div>

      <BuildupPanel data={data} />
      <MaxPainPanel data={data} />
      <StrikePickerPanel data={data} />
      <Placeholder title="Market Bias" />
      <Placeholder title="Pre-market Overview" />
    </aside>
  );
}

function BuildupPanel({ data }: { data: OptionsChainResponse }) {
  const a = data.buildup_analysis;
  if (!a) {
    return <Placeholder title="Build-up Analysis" />;
  }
  return (
    <div className="rounded border border-white/10 px-2 py-2 text-[11px]" data-testid="options-buildup">
      <div className="mb-1 text-[10px] uppercase tracking-wide text-white/40">Build-up Analysis</div>
      <p className="font-medium text-white/80">{a.summary}</p>
      <div className="mt-1 font-mono text-[10px] text-white/50">
        Near-ATM ΔOI · C {formatOiLakhs(a.call_doi)} · P {formatOiLakhs(a.put_doi)}
      </div>
      <div className="mt-1 text-[10px] text-white/40">
        Top Call +: {a.top_call_additions.map((x) => formatStrike(x.strike)).join(", ")}
      </div>
      <div className="text-[10px] text-white/40">
        Top Put +: {a.top_put_additions.map((x) => formatStrike(x.strike)).join(", ")}
      </div>
    </div>
  );
}

function MaxPainPanel({ data }: { data: OptionsChainResponse }) {
  const mp = data.max_pain;
  const em = data.expected_move;
  return (
    <div className="rounded border border-white/10 px-2 py-2 text-[11px]" data-testid="options-maxpain-panel">
      <div className="mb-1 text-[10px] uppercase tracking-wide text-white/40">Max Pain & Expected Move</div>
      {mp ? (
        <p className="font-mono text-white/80">
          Max Pain {formatStrike(mp.strike)}{" "}
          <span className="text-white/45">
            ({(mp.distance ?? mp.strike - (data.spot ?? 0)) > 0 ? "+" : ""}
            {formatStrike(mp.distance ?? mp.strike - (data.spot ?? 0))} pts)
          </span>
        </p>
      ) : (
        <p className="text-white/40">Max Pain unavailable</p>
      )}
      {em ? (
        <div className="mt-1 space-y-0.5 font-mono text-[10px] text-white/65" data-testid="options-expected-move">
          <div>
            Straddle ±{formatStrike(em.move)} → {formatStrike(em.lower)} / {formatStrike(em.upper)}
          </div>
          {em.iv ? (
            <div className="text-white/40">
              IV check ±{formatStrike(em.iv.move)} → {formatStrike(em.iv.lower)} / {formatStrike(em.iv.upper)}
            </div>
          ) : null}
        </div>
      ) : (
        <p className="mt-1 text-[10px] text-white/30">Expected move unavailable</p>
      )}
    </div>
  );
}

function StrikePickerPanel({ data }: { data: OptionsChainResponse }) {
  const picker = data.strike_picker;
  const spot = data.spot ?? 0;
  const [direction, setDirection] = useState<StrikeDirection>("Bullish");
  const [hold, setHold] = useState<HoldMode>("1d");

  const side: StrikePickerSide | null = useMemo(() => {
    if (!picker) return null;
    const base = direction === "Bullish" ? picker.Bullish : picker.Bearish;
    if (!base) return null;
    const tHold = picker.t_holds?.[hold] ?? base.t_hold;
    return repricePickerSide(base, spot, tHold, hold);
  }, [picker, direction, hold, spot]);

  if (!picker || !side) {
    return <Placeholder title="Strike Picker" />;
  }

  return (
    <div className="rounded border border-white/10 px-2 py-2 text-[11px]" data-testid="options-strike-picker">
      <div className="mb-1 text-[10px] uppercase tracking-wide text-white/40">Strike Picker</div>
      <div className="mb-2 flex flex-wrap gap-1">
        {(["Bullish", "Bearish"] as StrikeDirection[]).map((d) => (
          <button
            key={d}
            type="button"
            className={`rounded px-2 py-0.5 text-[10px] ${
              direction === d ? "bg-white/15 text-white" : "text-white/45"
            }`}
            onClick={() => setDirection(d)}
          >
            {d}
          </button>
        ))}
        {(["intraday", "1d", "2d"] as HoldMode[]).map((m) => (
          <button
            key={m}
            type="button"
            className={`rounded px-2 py-0.5 text-[10px] ${
              hold === m ? "bg-sky-500/20 text-sky-100" : "text-white/45"
            }`}
            onClick={() => setHold(m)}
            title="θ · t_hold horizon"
          >
            {m === "intraday" ? "Intraday" : m === "1d" ? "1 day" : "2 days"}
          </button>
        ))}
      </div>
      <div className="mb-1 font-mono text-[10px] text-white/55">
        Target {side.target != null ? formatStrike(side.target) : "—"} · Stop{" "}
        {side.stop != null ? formatStrike(side.stop) : "—"}
        {side.lot_size != null ? ` · lot ${side.lot_size}` : ""}
      </div>
      {side.stop_why ? (
        <p className="mb-2 text-[9px] leading-snug text-white/35" data-testid="strike-stop-why">
          Stop: {side.stop_why}
        </p>
      ) : null}
      <div className="flex flex-col gap-1.5">
        {side.candidates.map((c) => (
          <div key={c.bucket} className="rounded border border-white/5 bg-white/[0.02] px-1.5 py-1">
            <div className="flex justify-between font-mono text-white/80">
              <span>
                {c.bucket} {formatStrike(c.strike)} {c.side === "call" ? "CE" : "PE"}
              </span>
              <span>{formatSpot(c.ltp)}</span>
            </div>
            <div className="mt-0.5 grid grid-cols-2 gap-x-2 text-[9px] text-white/45">
              <span>Δ {c.delta.toFixed(3)}</span>
              <span>Γ {c.gamma.toFixed(4)}</span>
              <span>θ {c.theta.toFixed(2)}/d</span>
              <span>θ% {c.theta_pct_premium != null ? `${c.theta_pct_premium.toFixed(1)}%` : "—"}</span>
              <span>IV {formatIv(c.iv)}</span>
              <span>Spr {formatPct(c.spread_pct)}</span>
              <span>Cost/lot {c.cost_per_lot != null ? formatSpot(c.cost_per_lot) : "—"}</span>
              <span>t_hold {side.t_hold.toFixed(2)}d</span>
              <span>@tgt {c.est_at_target != null ? formatSpot(c.est_at_target) : "—"}</span>
              <span>@stop {c.est_at_stop != null ? formatSpot(c.est_at_stop) : "—"}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function IvPanel({ data }: { data: OptionsChainResponse }) {
  const iv = data.iv_panel;
  if (!iv) return null;
  const verdictTone =
    iv.verdict === "Options expensive"
      ? "text-red-300"
      : iv.verdict === "Options cheap"
        ? "text-emerald-300"
        : "text-white/70";
  return (
    <div
      className="min-w-[11rem] rounded border border-white/10 bg-white/[0.03] px-3 py-2"
      data-testid="options-iv-panel"
    >
      <div className="text-[10px] uppercase tracking-wide text-white/40">IV</div>
      <div className="font-mono text-sm text-white/90">
        ATM {formatIv(iv.atm_iv)}
        {iv.india_vix != null ? (
          <span className="ml-1 text-[11px] text-white/45">vs VIX {formatIv(iv.india_vix)}</span>
        ) : null}
      </div>
      <div className={`text-[10px] ${verdictTone}`}>{iv.verdict ?? "—"}</div>
      <div className="mt-0.5 text-[10px] text-white/40">
        Skew {iv.skew != null ? formatChg(iv.skew, 1) : "—"}
        <span className="mx-1 text-white/20">·</span>
        {iv.percentile.label}
        {!iv.percentile.available ? (
          <span className="text-white/30"> ({iv.percentile.days}/{iv.percentile.need}d)</span>
        ) : null}
      </div>
    </div>
  );
}

function pcrTone(read: PcrValue["read"]): string {
  if (read === "Bullish" || read === "Strong Bullish") return "text-emerald-300";
  if (read === "Bearish" || read === "Strong Bearish") return "text-red-300";
  return "text-white/35";
}

function formatNetDoiLakhs(net: number): string {
  const abs = formatOiLakhs(Math.abs(net));
  if (net > 0) return `+${abs}`;
  if (net < 0) return `−${abs}`;
  return abs;
}

function PcrCard({ label, pcr }: { label: string; pcr?: PcrValue }) {
  const oneSided = Boolean(pcr?.one_sided);
  const value =
    pcr == null
      ? "—"
      : pcr.value == null
        ? "n/a"
        : oneSided
          ? "One-sided"
          : typeof pcr.display === "number"
            ? pcr.display.toFixed(2)
            : String(pcr.display);
  const read = pcr?.read;
  const tooltip =
    pcr?.value != null
      ? `Exact PCR ${pcr.value.toFixed(4)}`
      : pcr?.reason ?? undefined;
  let sub: string | undefined;
  if (pcr?.value == null) {
    sub = pcr?.reason ?? undefined;
  } else if (oneSided && pcr.capped_label) {
    const net =
      pcr.net_doi != null ? ` · net ΔOI ${formatNetDoiLakhs(pcr.net_doi)}` : "";
    sub = `${pcr.capped_label}${net}`;
  } else if (read) {
    sub = read;
  }
  return (
    <div
      className="min-w-[7.5rem] max-w-[14rem] rounded border border-white/10 bg-white/[0.03] px-3 py-2"
      title={tooltip}
      data-testid={`pcr-card-${label}`}
    >
      <div className="text-[10px] uppercase tracking-wide text-white/40">{label}</div>
      <div className="font-mono text-sm text-white/90">{value}</div>
      {sub ? <div className={`text-[10px] ${pcrTone(read)}`}>{sub}</div> : null}
      {pcr?.concentration ? (
        <div className="mt-0.5 text-[9px] leading-snug text-amber-200/70" data-testid="pcr-concentration">
          {pcr.concentration}
        </div>
      ) : null}
    </div>
  );
}

function MajorWallLine({
  kind,
  wall,
  spot,
}: {
  kind: "Call" | "Put";
  wall?: { strike: number; oi: number } | null;
  spot: number;
}) {
  if (!wall) {
    return (
      <div className="flex justify-between py-0.5 font-mono text-[11px] text-white/35">
        <span>{kind}</span>
        <span>—</span>
      </div>
    );
  }
  const dist = wall.strike - spot;
  return (
    <div className="flex justify-between gap-2 py-0.5 font-mono text-[11px] text-white/75">
      <span className={kind === "Call" ? "text-red-300/90" : "text-emerald-300/90"}>{kind}</span>
      <span className="text-right">
        {formatStrike(wall.strike)}
        <span className="ml-1 text-white/35">
          {formatOiLakhs(wall.oi)} · {dist > 0 ? "+" : ""}
          {formatStrike(dist)} pts
        </span>
      </span>
    </div>
  );
}

function Placeholder({ title }: { title: string }) {
  return (
    <div className="rounded border border-dashed border-white/10 px-2 py-3 text-[11px] text-white/30">
      {title}
    </div>
  );
}

function ColumnsToggle({
  greeks,
  setGreeks,
}: {
  greeks: Record<GreekCol, boolean>;
  setGreeks: (next: Record<GreekCol, boolean>) => void;
}) {
  const labels: Record<GreekCol, string> = {
    delta: "Δ",
    gamma: "Γ",
    theta: "Θ",
    vega: "ν",
  };
  return (
    <div className="flex items-center gap-1 text-[10px] text-white/45" data-testid="options-columns-toggle">
      <span className="mr-1 uppercase tracking-wide">Columns</span>
      {(Object.keys(labels) as GreekCol[]).map((k) => (
        <button
          key={k}
          type="button"
          className={`rounded px-1.5 py-0.5 ${greeks[k] ? "bg-white/15 text-white" : "text-white/35"}`}
          onClick={() => setGreeks({ ...greeks, [k]: !greeks[k] })}
        >
          {labels[k]}
        </button>
      ))}
    </div>
  );
}

function ChainTable({
  data,
  greeks,
}: {
  data: OptionsChainResponse;
  greeks: Record<GreekCol, boolean>;
}) {
  const maxOi = data.max_oi ?? 1;
  const greekHeads = (
    [
      ["delta", "Δ"],
      ["gamma", "Γ"],
      ["theta", "Θ"],
      ["vega", "ν"],
    ] as const
  ).filter(([k]) => greeks[k]);
  const callHeads = ["OI", "Chg OI", "Vol", "IV", "LTP", "Chg", "Bid", "Ask", "Spr%", ...greekHeads.map(([, h]) => h)];
  const putHeads = [...callHeads].reverse();
  const colSpan = callHeads.length;
  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <table className="w-full border-collapse text-[11px]">
        <thead className="sticky top-0 z-10 bg-[#0b0e14]">
          <tr className="border-b border-white/10 text-white/40">
            <th
              colSpan={colSpan}
              className="px-2 py-1 text-center text-[10px] uppercase tracking-wide text-emerald-300/80"
            >
              Calls
            </th>
            <th className="px-2 py-1 text-center text-[10px] uppercase tracking-wide text-white/50">Strike</th>
            <th
              colSpan={colSpan}
              className="px-2 py-1 text-center text-[10px] uppercase tracking-wide text-red-300/80"
            >
              Puts
            </th>
          </tr>
          <tr className="border-b border-white/10 text-[10px] text-white/35">
            {callHeads.map((h) => (
              <th key={`c-${h}`} className="px-1.5 py-1 text-right font-normal">
                {h}
              </th>
            ))}
            <th className="px-2 py-1 text-center font-normal"> </th>
            {putHeads.map((h) => (
              <th key={`p-${h}`} className="px-1.5 py-1 text-right font-normal">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.rows.map((row) => (
            <ChainRow key={row.strike} row={row} maxOi={maxOi} spot={data.spot ?? 0} greeks={greeks} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ChainRow({
  row,
  maxOi,
  spot,
  greeks,
}: {
  row: OptionsRow;
  maxOi: number;
  spot: number;
  greeks: Record<GreekCol, boolean>;
}) {
  const atm = row.atm;
  const base = atm ? "bg-amber-400/10" : "hover:bg-white/[0.02]";
  return (
    <tr className={`border-b border-white/5 ${base}`} data-atm={atm ? "1" : undefined}>
      <LegCells leg={row.call} maxOi={maxOi} itm={row.call_itm} greeks={greeks} />
      <td
        className={`px-2 py-1 text-center font-mono text-xs ${
          atm ? "font-semibold text-amber-200" : "text-white/85"
        }`}
      >
        <div className="flex items-center justify-center gap-1">
          {row.tag ? (
            <span
              className={`rounded px-1 text-[9px] ${
                row.tag === "PIVOT"
                  ? "bg-sky-500/20 text-sky-200"
                  : row.tag.startsWith("R")
                    ? "bg-red-500/20 text-red-300"
                    : "bg-emerald-500/20 text-emerald-300"
              }`}
              title={row.strength ? `${row.tag} · ${row.strength}` : row.tag}
            >
              {row.tag}
              {row.edge ? <span className="ml-0.5 opacity-80">·edge</span> : null}
            </span>
          ) : null}
          <span>{formatStrike(row.strike)}</span>
          {row.max_pain ? (
            <span className="rounded bg-violet-500/20 px-1 text-[9px] text-violet-200">MP</span>
          ) : null}
        </div>
        {atm ? <div className="text-[9px] text-amber-300/70">ATM · spot {formatSpot(spot)}</div> : null}
      </td>
      <LegCells leg={row.put} maxOi={maxOi} mirror itm={row.put_itm} greeks={greeks} />
    </tr>
  );
}

export function OptionsTab() {
  const data = useOptionsStore((s) => s.data);
  const expiry = useOptionsStore((s) => s.expiry);
  const error = useOptionsStore((s) => s.error);
  const loading = useOptionsStore((s) => s.loading);
  const setExpiry = useOptionsStore((s) => s.setExpiry);
  const load = useOptionsStore((s) => s.load);
  const timer = useRef<number | null>(null);
  const [greeks, setGreeks] = useState<Record<GreekCol, boolean>>(GREEK_DEFAULTS);

  useEffect(() => {
    const ac = new AbortController();
    void load(ac.signal);

    const tick = () => {
      const live = useOptionsStore.getState().data?.status === "LIVE";
      if (live) void load();
    };
    timer.current = window.setInterval(tick, POLL_MS);
    return () => {
      ac.abort();
      if (timer.current != null) window.clearInterval(timer.current);
    };
  }, [load, expiry]);

  const r1 = data ? levelByTag(data, "R1") : undefined;
  const s1 = data ? levelByTag(data, "S1") : undefined;
  const expiries = data?.expiries ?? (expiry ? [expiry] : []);

  return (
    <div className="flex h-full min-h-0 flex-col bg-[#0b0e14]" data-testid="options-tab">
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-white/10 px-3 py-2">
        <Card label="Spot" value={formatSpot(data?.spot)} />
        <Card label="ATM" value={data?.atm != null ? formatStrike(data.atm) : "—"} />
        {data?.available ? <IvPanel data={data} /> : null}
        <div className="flex flex-col gap-0.5">
          <div className="flex flex-wrap gap-2">
            <PcrCard label="PCR full" pcr={data?.pcrs?.full} />
            <PcrCard label="PCR near" pcr={data?.pcrs?.near_atm} />
            <PcrCard label="PCR ΔOI" pcr={data?.pcrs?.change_oi} />
          </div>
          {data?.pcr_mixed_signals ? (
            <div className="px-1 text-[10px] text-amber-200/80" data-testid="pcr-mixed-signals">
              Mixed signals
            </div>
          ) : null}
        </div>
        <Card label="Call OI" value={formatOiLakhs(data?.total_call_oi)} />
        <Card label="Put OI" value={formatOiLakhs(data?.total_put_oi)} />
        <Card label="R1" value={r1 ? formatStrike(r1.strike) : "—"} sub={r1 ? formatOiLakhs(r1.oi) : undefined} />
        <Card label="S1" value={s1 ? formatStrike(s1.strike) : "—"} sub={s1 ? formatOiLakhs(s1.oi) : undefined} />

        <label className="ml-2 flex items-center gap-1 text-xs text-white/60">
          Expiry
          <select
            className="rounded border border-white/15 bg-[#0b0e14] px-2 py-1 font-mono text-white/85"
            value={expiry ?? data?.expiry ?? ""}
            onChange={(e) => setExpiry(e.target.value)}
            data-testid="options-expiry"
          >
            {expiries.map((e) => (
              <option key={e} value={e}>
                {e}
              </option>
            ))}
          </select>
        </label>

        <ColumnsToggle greeks={greeks} setGreeks={setGreeks} />

        <div className="ml-auto flex items-center gap-3 text-xs text-white/50">
          {data?.as_of_ist ? (
            <span title={data.as_of_ist}>Updated {formatIstClock(data.as_of_ist)} IST</span>
          ) : null}
          {data?.from_snapshot ? <span className="text-white/35">snapshot</span> : null}
          {loading ? <span className="text-white/35">loading…</span> : null}
          <span
            className={`rounded px-2 py-0.5 text-[10px] font-semibold tracking-wide ${
              data?.status === "LIVE"
                ? "bg-emerald-500/15 text-emerald-300"
                : "bg-white/10 text-white/60"
            }`}
            data-testid="options-status"
          >
            {data?.status === "LIVE" ? "LIVE" : "MARKET CLOSED"}
          </span>
        </div>
      </div>

      {error ? (
        <div className="m-3 rounded border border-amber-400/40 bg-amber-400/10 px-3 py-2 text-sm text-amber-100">
          {error}
        </div>
      ) : null}

      {!data && !error ? (
        <div className="flex flex-1 items-center justify-center text-sm text-white/40">Loading option chain…</div>
      ) : null}

      {data && !data.available ? (
        <div className="m-3 rounded border border-white/10 px-3 py-2 text-sm text-white/60">
          {data.reason ?? "Option chain unavailable"}
        </div>
      ) : null}

      {data?.available ? (
        <div className="flex min-h-0 flex-1">
          <div className="flex min-h-0 min-w-0 flex-1 flex-col">
            <ChainTable data={data} greeks={greeks} />
            <OiBarChart data={data} />
            <TrendsPanel expiry={expiry ?? data.expiry} />
          </div>
          <KeyLevels data={data} />
        </div>
      ) : null}
    </div>
  );
}
