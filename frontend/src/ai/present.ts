export const ANALYSIS_LABEL = "AI analysis: context, not a trade signal";

export function canPlaceOrders(): false {
  return false;
}

export interface AnalysisView {
  trends: { "5m": string; "15m": string; "1h": string; "1D": string };
  bias: "bull" | "bear" | "neutral";
  key_levels: { price: number; kind: "support" | "resistance"; label: string }[];
  patterns: string[];
  bull: { trigger: number; invalidation: number; note: string };
  bear: { trigger: number; invalidation: number; note: string };
  confidence: number;
  reasoning: string;
}

export interface LevelLine {
  price: number;
  title: string;
  kind: "support" | "resistance" | "trigger" | "invalidation";
  color: string;
}

const COLOR = {
  support: "#26a69a",
  resistance: "#ef5350",
  trigger: "#f5c542",
  invalidation: "#94a3b8",
} as const;

export const HORIZONS = [
  { id: "60m", label: "60 minutes later" },
  { id: "session_close", label: "same-session close" },
  { id: "1", label: "1 session" },
  { id: "3", label: "3 sessions" },
  { id: "5", label: "5 sessions" },
] as const;

export interface HorizonRates {
  label: string;
  scored: number;
  ai: { bias: number | null; triggers: number | null; levels: number | null };
  alwaysBullish: { bias: number | null };
  followTrend: { bias: number | null };
}

export function levelLines(analysis: AnalysisView): LevelLine[] {
  const lines: LevelLine[] = analysis.key_levels.map((level) => ({
    price: level.price,
    title: level.label,
    kind: level.kind,
    color: COLOR[level.kind],
  }));
  lines.push(
    { price: analysis.bull.trigger, title: "Bull trigger", kind: "trigger", color: COLOR.trigger },
    { price: analysis.bull.invalidation, title: "Bull invalidation", kind: "invalidation", color: COLOR.invalidation },
    { price: analysis.bear.trigger, title: "Bear trigger", kind: "trigger", color: COLOR.trigger },
    { price: analysis.bear.invalidation, title: "Bear invalidation", kind: "invalidation", color: COLOR.invalidation },
  );
  return lines;
}

function percent(value: number | null): string {
  if (value === null) return "—";
  return `${Math.round(value * 100)}%`;
}

export function hitRateText(row: HorizonRates): string[] {
  return [
    `${row.label} · scored ${row.scored}`,
    `AI bias ${percent(row.ai.bias)} · always bullish ${percent(row.alwaysBullish.bias)} · follow the trend ${percent(row.followTrend.bias)}`,
    `AI triggers ${percent(row.ai.triggers)}`,
    `AI levels ${percent(row.ai.levels)}`,
  ];
}

export function costText(
  usage: { prompt_tokens: number; completion_tokens: number },
  rates: { inputUsdPerMtok?: number; outputUsdPerMtok?: number },
): string {
  const text = `${usage.prompt_tokens} in / ${usage.completion_tokens} out`;
  if (rates.inputUsdPerMtok === undefined || rates.outputUsdPerMtok === undefined) return text;
  const dollars =
    (usage.prompt_tokens * rates.inputUsdPerMtok + usage.completion_tokens * rates.outputUsdPerMtok) / 1_000_000;
  return `${text} · $${dollars.toFixed(6)}`;
}
