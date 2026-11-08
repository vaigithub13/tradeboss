import { useEffect, useRef } from "react";

import type { OptionsChainResponse, OptionsLeg, OptionsLevel, OptionsRow } from "../api/options";
import { useOptionsStore } from "../store/optionsStore";
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

const POLL_MS = 60_000;

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

function LegCells({
  leg,
  maxOi,
  mirror,
  itm,
}: {
  leg: OptionsLeg;
  maxOi: number;
  mirror?: boolean;
  itm?: boolean;
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
  const chg = (
    <td key="chg" className={`px-1.5 py-1 text-right font-mono ${shade} ${chgClass(leg.price_chg)}`}>
      {formatChg(leg.price_chg)}
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
  const cells = [oi, chgOi, vol, iv, ltp, chg, bid, ask, spread];
  return <>{mirror ? cells.reverse() : cells}</>;
}

function levelByTag(data: OptionsChainResponse, tag: string): OptionsLevel | undefined {
  return [...(data.resistance ?? []), ...(data.support ?? [])].find((l) => l.tag === tag);
}

function KeyLevels({ data }: { data: OptionsChainResponse }) {
  const spot = data.spot ?? 0;
  const ladder = buildKeyLevelsLadder(data);
  return (
    <aside className="flex w-52 shrink-0 flex-col gap-2 border-l border-white/10 bg-[#0b0f14] p-3 text-xs">
      <h2 className="text-sm font-semibold text-white">Key Levels</h2>
      <ul className="flex flex-col gap-1">
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
                <p className="mt-1 text-[10px] leading-snug text-sky-100/70">{p.read}</p>
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
            </li>
          );
        })}
      </ul>

      <div className="rounded border border-white/10 px-2 py-2">
        <div className="mb-1 text-[10px] uppercase tracking-wide text-white/40">Major walls</div>
        <MajorWallLine kind="Call" wall={data.major_walls?.call} spot={spot} />
        <MajorWallLine kind="Put" wall={data.major_walls?.put} spot={spot} />
      </div>

      <Placeholder title="Build-up Analysis" />
      <Placeholder title="Max Pain & Expected Move" />
      <Placeholder title="Strike Picker" />
      <Placeholder title="Market Bias" />
      <Placeholder title="Pre-market Overview" />
    </aside>
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

function ChainTable({ data }: { data: OptionsChainResponse }) {
  const maxOi = data.max_oi ?? 1;
  const callHeads = ["OI", "Chg OI", "Vol", "IV", "LTP", "Chg", "Bid", "Ask", "Spr%"];
  const putHeads = [...callHeads].reverse();
  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <table className="w-full border-collapse text-[11px]">
        <thead className="sticky top-0 z-10 bg-[#0b0e14]">
          <tr className="border-b border-white/10 text-white/40">
            <th colSpan={9} className="px-2 py-1 text-center text-[10px] uppercase tracking-wide text-emerald-300/80">
              Calls
            </th>
            <th className="px-2 py-1 text-center text-[10px] uppercase tracking-wide text-white/50">Strike</th>
            <th colSpan={9} className="px-2 py-1 text-center text-[10px] uppercase tracking-wide text-red-300/80">
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
            <ChainRow key={row.strike} row={row} maxOi={maxOi} spot={data.spot ?? 0} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ChainRow({ row, maxOi, spot }: { row: OptionsRow; maxOi: number; spot: number }) {
  const atm = row.atm;
  const base = atm ? "bg-amber-400/10" : "hover:bg-white/[0.02]";
  return (
    <tr className={`border-b border-white/5 ${base}`} data-atm={atm ? "1" : undefined}>
      <LegCells leg={row.call} maxOi={maxOi} itm={row.call_itm} />
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
        </div>
        {atm ? <div className="text-[9px] text-amber-300/70">ATM · spot {formatSpot(spot)}</div> : null}
      </td>
      <LegCells leg={row.put} maxOi={maxOi} mirror itm={row.put_itm} />
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
        <Card label="PCR" value={data?.pcr != null ? data.pcr.toFixed(2) : "—"} />
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
          <ChainTable data={data} />
          <KeyLevels data={data} />
        </div>
      ) : null}
    </div>
  );
}
