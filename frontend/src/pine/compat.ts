/**
 * Static Pine scan: unsupported calls (with line numbers), repaint warnings, and
 * source rewrites so PineTS can run chart OHLCV (syminfo.tickerid, index VWAP volume).
 */

export const SUPPORTED_CALLS = [
  "ta.ema",
  "ta.sma",
  "ta.rsi",
  "ta.atr",
  "ta.dmi",
  "ta.crossover",
  "ta.crossunder",
  "ta.highest",
  "ta.lowest",
  "ta.vwap",
  "timeframe.change",
  "timeframe.period",
  "barstate.isconfirmed",
  "time",
  "input.session",
  "request.security",
  "plotshape",
  "plotchar",
  "plot",
  "alertcondition",
  "alert",
  "strategy.entry",
  "strategy.close",
  "strategy.close_all",
  "strategy.exit",
  "strategy.position_size",
  "strategy.closedtrades",
  "strategy.closedtrades.profit",
] as const;

/** Calls we know PineTS / our host do not support — report line N, never silent. */
const UNSUPPORTED_PATTERNS: { re: RegExp; name: string }[] = [
  { re: /\brequest\.security_lower_tf\s*\(/g, name: "request.security_lower_tf" },
  { re: /\brequest\.seed\s*\(/g, name: "request.seed" },
  { re: /\bticker\.modify\s*\(/g, name: "ticker.modify" },
  { re: /\bstrategy\.order\s*\(/g, name: "strategy.order" },
  { re: /\bruntime\.error\s*\(/g, name: "runtime.error" },
  { re: /\barray\.(from_json|to_json)\s*\(/g, name: "array json" },
];

export interface UnsupportedHit {
  line: number;
  call: string;
  message: string;
}

export interface RepaintHit {
  line: number;
  reason: string;
}

export interface CompatReport {
  unsupported: UnsupportedHit[];
  repaint: RepaintHit[];
  notices: string[];
}

function lineOf(source: string, index: number): number {
  return source.slice(0, index).split("\n").length;
}

/** Detect unsupported calls and repaint risks in Pine source. */
export function scanCompat(source: string): CompatReport {
  const unsupported: UnsupportedHit[] = [];
  const repaint: RepaintHit[] = [];
  const notices: string[] = [];

  for (const { re, name } of UNSUPPORTED_PATTERNS) {
    re.lastIndex = 0;
    let m: RegExpExecArray | null;
    while ((m = re.exec(source))) {
      unsupported.push({
        line: lineOf(source, m.index),
        call: name,
        message: `line ${lineOf(source, m.index)}: ${name} not supported`,
      });
    }
  }

  // request.security(..., lookahead_on) without [1] on the expression → may repaint
  const secRe =
    /request\.security\s*\(\s*([^,]+)\s*,\s*([^,]+)\s*,\s*([^,]+?)(\s*,[^)]*)?\)/g;
  let sm: RegExpExecArray | null;
  while ((sm = secRe.exec(source))) {
    const full = sm[0];
    const expr = (sm[3] ?? "").trim();
    const lookaheadOn =
      /lookahead\s*=\s*barmerge\.lookahead_on/.test(full) ||
      /barmerge\.lookahead_on/.test(full);
    if (lookaheadOn && !/\[\s*1\s*\]\s*$/.test(expr) && !/\[\s*1\s*\]/.test(expr)) {
      repaint.push({
        line: lineOf(source, sm.index),
        reason: `line ${lineOf(source, sm.index)}: may repaint — request.security with lookahead_on without [1] on the expression`,
      });
    }
  }

  const realtime = /\bbarstate\.isrealtime\b/g;
  let rm: RegExpExecArray | null;
  while ((rm = realtime.exec(source))) {
    repaint.push({
      line: lineOf(source, rm.index),
      reason: `line ${lineOf(source, rm.index)}: may repaint — barstate.isrealtime`,
    });
  }

  const varip = /\bvarip\b/g;
  let vm: RegExpExecArray | null;
  while ((vm = varip.exec(source))) {
    repaint.push({
      line: lineOf(source, vm.index),
      reason: `line ${lineOf(source, vm.index)}: may repaint — varip`,
    });
  }

  if (/\bta\.vwap\s*\(/.test(source)) {
    notices.push("ta.vwap: on NIFTY/SENSEX index, futures volume is injected when available");
  }

  return { unsupported, repaint, notices };
}

/** Map TradeBoss timeframe labels to PineTS chart timeframe strings. */
export function toPineTimeframe(tf: string): string {
  const map: Record<string, string> = {
    "1m": "1",
    "3m": "3",
    "5m": "5",
    "15m": "15",
    "30m": "30",
    "1h": "60",
    "1D": "D",
    "1W": "W",
  };
  return map[tf] ?? tf.replace(/m$/, "");
}

/**
 * Rewrite source so PineTS can run on array OHLCV:
 * - syminfo.tickerid → literal ticker (PineTS leaves tickerid empty on custom data)
 * - optional: leave as-is otherwise
 */
export function rewriteForRuntime(source: string, ticker: string): string {
  const lit = JSON.stringify(ticker);
  return source.replace(/\bsyminfo\.tickerid\b/g, lit);
}

export interface OhlcvBar {
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  openTime: number; // ms
}

/** Build PineTS OHLCV; inject futures volumes when index volume is zero. */
export function prepareOhlcv(
  candles: readonly { time: number; open: number; high: number; low: number; close: number; volume: number }[],
  futuresVolume: readonly number[] | null,
): { bars: OhlcvBar[]; vwapNotice: string | null } {
  const allZero = candles.every((c) => !c.volume);
  let vwapNotice: string | null = null;
  const bars: OhlcvBar[] = candles.map((c, i) => {
    let volume = c.volume;
    if (allZero) {
      const fv = futuresVolume?.[i];
      if (fv != null && fv > 0) {
        volume = fv;
      }
    }
    return {
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
      volume,
      openTime: c.time * 1000,
    };
  });
  if (allZero) {
    if (futuresVolume && futuresVolume.some((v) => v > 0)) {
      vwapNotice = "ta.vwap uses front-month futures volume (index has no volume)";
    } else {
      vwapNotice = "ta.vwap unavailable: no futures volume for this range (returns na)";
    }
  }
  return { bars, vwapNotice };
}
