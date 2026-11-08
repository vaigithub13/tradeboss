import { useEffect, useState } from "react";

import { fetchOptionsTrends, type OptionsTrendsResponse } from "../api/options";

const W = 280;
const H = 56;
const PAD = 4;

export function TrendsPanel({ expiry }: { expiry?: string | null }) {
  const [data, setData] = useState<OptionsTrendsResponse | null>(null);

  useEffect(() => {
    const ac = new AbortController();
    void fetchOptionsTrends(ac.signal, expiry ?? undefined)
      .then(setData)
      .catch(() =>
        setData({
          day: "",
          available: false,
          message: "Collecting data — first point at 09:15",
          points: [],
        }),
      );
    return () => ac.abort();
  }, [expiry]);

  if (!data) {
    return (
      <div className="border-t border-white/10 px-3 py-2 text-[11px] text-white/35">Loading trends…</div>
    );
  }

  if (!data.available || data.points.length === 0) {
    return (
      <div className="border-t border-white/10 px-3 py-2 text-[11px] text-white/45" data-testid="options-trends">
        Intraday trends · {data.message ?? "Collecting data — first point at 09:15"}
      </div>
    );
  }

  return (
    <div className="flex flex-wrap gap-4 border-t border-white/10 px-3 py-2" data-testid="options-trends">
      <Spark title="Full PCR" values={data.points.map((p) => p.pcr_full)} />
      <Spark title="Near-ATM PCR" values={data.points.map((p) => p.pcr_near_atm)} />
      <Spark title="Spot" values={data.points.map((p) => p.spot)} />
    </div>
  );
}

function Spark({ title, values }: { title: string; values: Array<number | null | undefined> }) {
  const nums = values.filter((v): v is number => typeof v === "number" && Number.isFinite(v));
  if (nums.length < 2) {
    return (
      <div className="min-w-[140px]">
        <div className="text-[10px] text-white/40">{title}</div>
        <div className="text-[10px] text-white/30">need more points</div>
      </div>
    );
  }
  const min = Math.min(...nums);
  const max = Math.max(...nums);
  const span = max - min || 1;
  const pts = nums
    .map((v, i) => {
      const x = PAD + (i / (nums.length - 1)) * (W - PAD * 2);
      const y = H - PAD - ((v - min) / span) * (H - PAD * 2);
      return `${x},${y}`;
    })
    .join(" ");
  return (
    <div className="min-w-[140px]">
      <div className="mb-0.5 flex justify-between text-[10px] text-white/40">
        <span>{title}</span>
        <span className="font-mono text-white/60">
          {(nums[nums.length - 1] ?? 0).toFixed(title === "Spot" ? 0 : 2)}
        </span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="h-14 w-44">
        <polyline fill="none" stroke="rgba(125,211,252,0.85)" strokeWidth="1.5" points={pts} />
      </svg>
    </div>
  );
}
