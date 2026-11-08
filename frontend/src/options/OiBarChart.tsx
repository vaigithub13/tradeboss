import { useMemo, useState } from "react";

import type { OptionsChainResponse } from "../api/options";
import { formatStrike } from "./format";

type Mode = "oi" | "doi";

const W = 720;
const H = 220;
const PAD = { t: 16, r: 12, b: 28, l: 44 };

export function OiBarChart({ data }: { data: OptionsChainResponse }) {
  const [mode, setMode] = useState<Mode>("oi");
  const rows = data.rows;
  const spot = data.spot ?? 0;
  const maxOi = Math.max(data.max_oi ?? 1, 1);
  const maxDoi = Math.max(data.max_oi_chg ?? 1, 1);

  const geometry = useMemo(() => {
    const innerW = W - PAD.l - PAD.r;
    const innerH = H - PAD.t - PAD.b;
    const n = Math.max(rows.length, 1);
    const slot = innerW / n;
    const barW = Math.max(2, slot * 0.35);
    return { innerW, innerH, slot, barW, n };
  }, [rows.length]);

  const yScale = (v: number, peak: number) => (v / peak) * geometry.innerH;

  return (
    <div className="shrink-0 border-t border-white/10 px-3 py-2" data-testid="options-oi-chart">
      <div className="mb-1 flex items-center gap-2">
        <span className="text-[11px] font-semibold text-white/70">OI by strike</span>
        <div className="flex rounded border border-white/15 p-0.5 text-[10px]">
          <button
            type="button"
            className={`rounded px-2 py-0.5 ${mode === "oi" ? "bg-white/10 text-white" : "text-white/45"}`}
            onClick={() => setMode("oi")}
          >
            OI
          </button>
          <button
            type="button"
            className={`rounded px-2 py-0.5 ${mode === "doi" ? "bg-white/10 text-white" : "text-white/45"}`}
            onClick={() => setMode("doi")}
          >
            Change in OI
          </button>
        </div>
        <span className="text-[10px] text-white/35">Call green · Put red · ΔOI striped</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="h-52 w-full max-w-full" role="img">
        <line
          x1={PAD.l}
          x2={W - PAD.r}
          y1={H - PAD.b}
          y2={H - PAD.b}
          stroke="rgba(255,255,255,0.12)"
        />
        {rows.map((row, i) => {
          const cx = PAD.l + geometry.slot * (i + 0.5);
          const callVal = mode === "oi" ? row.call.oi : Math.abs(row.call.oi_chg);
          const putVal = mode === "oi" ? row.put.oi : Math.abs(row.put.oi_chg);
          const peak = mode === "oi" ? maxOi : maxDoi;
          const callH = yScale(callVal, peak);
          const putH = yScale(putVal, peak);
          const base = H - PAD.b;
          const callDrop = mode === "doi" && row.call.oi_chg < 0;
          const putDrop = mode === "doi" && row.put.oi_chg < 0;
          const prevCall =
            mode === "oi" && row.call.prev_oi > 0
              ? Math.min(row.call.oi, row.call.prev_oi)
              : null;
          const prevPut =
            mode === "oi" && row.put.prev_oi > 0 ? Math.min(row.put.oi, row.put.prev_oi) : null;
          return (
            <g key={row.strike}>
              {/* Call */}
              <rect
                x={cx - geometry.barW - 1}
                y={base - callH}
                width={geometry.barW}
                height={callH}
                fill={callDrop ? "rgba(239,68,68,0.55)" : "rgba(52,211,153,0.55)"}
              />
              {mode === "oi" && row.call.oi_chg !== 0 && prevCall != null ? (
                <rect
                  x={cx - geometry.barW - 1}
                  y={base - callH}
                  width={geometry.barW}
                  height={Math.max(0, callH - yScale(prevCall, peak))}
                  fill={row.call.oi_chg < 0 ? "rgba(239,68,68,0.35)" : "rgba(52,211,153,0.25)"}
                  stroke="rgba(255,255,255,0.15)"
                  strokeWidth={0.5}
                  strokeDasharray="2 1"
                />
              ) : null}
              {/* Put */}
              <rect
                x={cx + 1}
                y={base - putH}
                width={geometry.barW}
                height={putH}
                fill={putDrop ? "rgba(239,68,68,0.55)" : "rgba(248,113,113,0.55)"}
              />
              {mode === "oi" && row.put.oi_chg !== 0 && prevPut != null ? (
                <rect
                  x={cx + 1}
                  y={base - putH}
                  width={geometry.barW}
                  height={Math.max(0, putH - yScale(prevPut, peak))}
                  fill={row.put.oi_chg < 0 ? "rgba(239,68,68,0.35)" : "rgba(248,113,113,0.25)"}
                  stroke="rgba(255,255,255,0.15)"
                  strokeWidth={0.5}
                  strokeDasharray="2 1"
                />
              ) : null}
              {i % 5 === 0 || row.atm || row.tag ? (
                <text
                  x={cx}
                  y={H - 8}
                  textAnchor="middle"
                  className="fill-white/40"
                  style={{ fontSize: 8 }}
                >
                  {formatStrike(row.strike)}
                </text>
              ) : null}
              {row.tag && row.tag !== "PIVOT" ? (
                <text x={cx} y={PAD.t - 2} textAnchor="middle" style={{ fontSize: 7 }} className="fill-white/50">
                  {row.tag}
                </text>
              ) : null}
            </g>
          );
        })}
        {/* Spot / Pivot / Max Pain vertical guides */}
        <Guide
          label="Spot"
          color="#fbbf24"
          x={priceX(rows, spot, geometry)}
          h={H}
        />
        {data.pivot ? (
          <Guide label="P" color="#38bdf8" x={strikeX(rows, data.pivot.strike, geometry)} h={H} />
        ) : null}
        {data.max_pain ? (
          <Guide label="MP" color="#c084fc" x={strikeX(rows, data.max_pain.strike, geometry)} h={H} />
        ) : null}
      </svg>
    </div>
  );
}

function strikeX(
  rows: OptionsChainResponse["rows"],
  strike: number,
  geometry: { slot: number },
): number | null {
  const i = rows.findIndex((r) => r.strike === strike);
  if (i < 0) return null;
  return PAD.l + geometry.slot * (i + 0.5);
}

function priceX(
  rows: OptionsChainResponse["rows"],
  price: number,
  geometry: { slot: number },
): number | null {
  if (!rows.length) return null;
  // interpolate between neighbouring strikes
  const first = rows[0];
  const last = rows[rows.length - 1];
  if (!first || !last) return null;
  if (price <= first.strike) return PAD.l + geometry.slot * 0.5;
  if (price >= last.strike) return PAD.l + geometry.slot * (rows.length - 0.5);
  for (let i = 0; i < rows.length - 1; i++) {
    const a = rows[i]?.strike;
    const b = rows[i + 1]?.strike;
    if (a == null || b == null) continue;
    if (price >= a && price <= b) {
      const t = (price - a) / (b - a || 1);
      return PAD.l + geometry.slot * (i + 0.5 + t);
    }
  }
  return null;
}

function Guide({
  label,
  color,
  x,
  h,
}: {
  label: string;
  color: string;
  x: number | null;
  h: number;
}) {
  if (x == null) return null;
  return (
    <g>
      <line x1={x} x2={x} y1={PAD.t} y2={h - PAD.b} stroke={color} strokeWidth={1} strokeDasharray="3 2" opacity={0.8} />
      <text x={x + 3} y={PAD.t + 8} style={{ fontSize: 8 }} fill={color}>
        {label}
      </text>
    </g>
  );
}
