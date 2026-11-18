/**
 * Shared PineTS runner used by the Web Worker (and by tests on the main thread).
 * Only closed bars are passed in — never the forming tip.
 */
import { PineTS } from "pinets";

import { prepareOhlcv, rewriteForRuntime, toPineTimeframe, type OhlcvBar } from "./compat";
import { extractSignals, type PineSignal } from "./signals";

export const MAX_MS_PER_BAR = 200;

export interface RunRequest {
  scriptId: string;
  source: string;
  ticker: string;
  timeframe: string;
  /** Closed bars only (unix seconds). */
  candles: readonly {
    time: number;
    open: number;
    high: number;
    low: number;
    close: number;
    volume: number;
  }[];
  futuresVolume?: readonly number[] | null;
}

export interface RunResult {
  scriptId: string;
  signals: PineSignal[];
  notices: string[];
  msPerBar: number;
  error?: string;
}

export async function runPineScript(req: RunRequest): Promise<RunResult> {
  const notices: string[] = [];
  if (req.candles.length === 0) {
    return { scriptId: req.scriptId, signals: [], notices, msPerBar: 0 };
  }

  const { bars, vwapNotice } = prepareOhlcv(req.candles, req.futuresVolume ?? null);
  if (vwapNotice) notices.push(vwapNotice);

  const source = rewriteForRuntime(req.source, req.ticker);
  const tf = toPineTimeframe(req.timeframe);
  const t0 = performance.now();

  try {
    const pine = new PineTS(bars as OhlcvBar[], req.ticker, tf);
    pine.setAlertMode("all");
    pine.setTimezone("Asia/Kolkata");
    const ctx = await pine.run(source);
    const ms = performance.now() - t0;
    const msPerBar = ms / req.candles.length;
    if (msPerBar > MAX_MS_PER_BAR) {
      return {
        scriptId: req.scriptId,
        signals: [],
        notices,
        msPerBar,
        error: `slow script: ${msPerBar.toFixed(1)} ms/bar (limit ${MAX_MS_PER_BAR}) — disabled`,
      };
    }
    for (const w of ctx.warnings ?? []) {
      if (w.message) notices.push(w.message);
    }
    const closes = req.candles.map((c) => ({ time: c.time, close: c.close }));
    const signals = extractSignals(ctx, closes);
    return { scriptId: req.scriptId, signals, notices, msPerBar };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return {
      scriptId: req.scriptId,
      signals: [],
      notices,
      msPerBar: (performance.now() - t0) / Math.max(1, req.candles.length),
      error: message,
    };
  }
}
