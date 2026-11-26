import type { ChartSnap } from "./assist";

/** Last candles, the channel lines, and the signal candle. Facts only. */
export function AssistChart({ chart }: { chart: ChartSnap | null | undefined }) {
  if (!chart || chart.candles.length === 0) return null;
  const width = 360;
  const height = 140;
  const pad = 10;
  const prices = chart.candles.flatMap((c) => [c.high, c.low]);
  if (chart.channel_high != null) prices.push(chart.channel_high);
  if (chart.channel_low != null) prices.push(chart.channel_low);
  const hi = Math.max(...prices);
  const lo = Math.min(...prices);
  const span = hi - lo || 1;
  const y = (price: number) => pad + ((hi - price) / span) * (height - pad * 2);
  const slot = (width - pad * 2) / chart.candles.length;
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className="w-full rounded bg-black/40"
      role="img"
      aria-label="Signal chart"
      data-testid="assist-chart"
    >
      {chart.channel_high != null && (
        <line x1={pad} x2={width - pad} y1={y(chart.channel_high)} y2={y(chart.channel_high)} stroke="#38bdf8" strokeDasharray="3 3" />
      )}
      {chart.channel_low != null && (
        <line x1={pad} x2={width - pad} y1={y(chart.channel_low)} y2={y(chart.channel_low)} stroke="#f59e0b" strokeDasharray="3 3" />
      )}
      {chart.candles.map((candle, i) => {
        const x = pad + i * slot + slot / 2;
        const up = candle.close >= candle.open;
        const marked = candle.time === chart.signal_time;
        return (
          <g key={candle.time} data-testid={marked ? "assist-signal-candle" : undefined}>
            <line
              x1={x}
              x2={x}
              y1={y(candle.high)}
              y2={y(candle.low)}
              stroke={marked ? "#f8fafc" : up ? "#34d399" : "#f87171"}
            />
            <rect
              x={x - Math.max(1, slot * 0.3)}
              width={Math.max(2, slot * 0.6)}
              y={y(Math.max(candle.open, candle.close))}
              height={Math.max(1, Math.abs(y(candle.open) - y(candle.close)))}
              fill={marked ? "#f8fafc" : up ? "#34d399" : "#f87171"}
            />
          </g>
        );
      })}
    </svg>
  );
}
