import { useEffect, useRef } from "react";
import { createChart, LineSeries, type IChartApi, type ISeriesApi, type UTCTimestamp } from "lightweight-charts";

import type { EquityPoint } from "./dashboard";
import { ACTIVE_PAPER_SLOTS, slotLabel } from "./slots";

const COLORS: Record<string, string> = {
  "2": "#38bdf8",
  "4": "#a78bfa",
  "5": "#34d399",
  "6": "#f472b6",
};

/** Cumulative net ₹ lines for active slots. */
export function PaperEquityChart({
  series,
  visible,
  onToggle,
}: {
  series: EquityPoint[];
  visible: Record<string, boolean>;
  onToggle: (slot: string) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const linesRef = useRef<Record<string, ISeriesApi<"Line">>>({});

  useEffect(() => {
    const el = host.current;
    if (!el) return;
    const chart = createChart(el, {
      layout: { background: { color: "transparent" }, textColor: "rgba(255,255,255,0.55)" },
      grid: { vertLines: { color: "rgba(255,255,255,0.04)" }, horzLines: { color: "rgba(255,255,255,0.04)" } },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false },
      height: 240,
      width: el.clientWidth,
    });
    chartRef.current = chart;
    const lines: Record<string, ISeriesApi<"Line">> = {};
    for (const slot of ACTIVE_PAPER_SLOTS) {
      lines[slot] = chart.addSeries(LineSeries, {
        color: COLORS[slot],
        lineWidth: 2,
        title: slotLabel(slot),
      });
    }
    linesRef.current = lines;
    const ro = new ResizeObserver(() => {
      if (host.current) chart.applyOptions({ width: host.current.clientWidth });
    });
    ro.observe(el);
    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
      linesRef.current = {};
    };
  }, []);

  useEffect(() => {
    for (const slot of ACTIVE_PAPER_SLOTS) {
      const line = linesRef.current[slot];
      if (!line) continue;
      line.applyOptions({ visible: visible[slot] !== false });
      line.setData(
        series.map((p) => ({
          time: p.time as UTCTimestamp,
          value: p.bySlot[slot] ?? 0,
        })),
      );
    }
    chartRef.current?.timeScale().fitContent();
  }, [series, visible]);

  if (!series.length) {
    return (
      <div className="flex h-60 items-center justify-center rounded border border-white/10 text-sm text-white/40" data-testid="equity-empty">
        No forward-test trades yet
      </div>
    );
  }

  return (
    <div data-testid="paper-equity-chart">
      <div className="mb-2 flex flex-wrap gap-3 text-[11px]">
        {ACTIVE_PAPER_SLOTS.map((slot) => (
          <label key={slot} className="flex cursor-pointer items-center gap-1.5 text-white/70">
            <input
              type="checkbox"
              checked={visible[slot] !== false}
              onChange={() => onToggle(slot)}
            />
            <span className="inline-block h-2 w-2 rounded-full" style={{ background: COLORS[slot] }} />
            {slot} · {slotLabel(slot)}
          </label>
        ))}
      </div>
      <div ref={host} className="w-full" />
    </div>
  );
}
