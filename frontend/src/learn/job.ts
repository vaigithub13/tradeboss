/** One incremental pass. The worker keeps `state` between candle closes. */

import { readLesson, type Lesson } from "./lesson";
import { buildStudy, type Study } from "./study";
import { readSignalBook, type SignalBook } from "./signals";
import { readLevels, type LevelLine } from "./levels";
import {
  emptyBuild,
  extendStructure,
  trendOf,
  type LearnBar,
  type Structure,
  type StructureBuild,
} from "./structure";

export interface LearnJob {
  bars: LearnBar[];
  left: number;
  right: number;
  rangeSwings: number;
  timeframe?: string;
  symbol?: string;
  /** Clock for the banner. The forming candle may set this; it is not one of `bars`. */
  now?: number;
  /** Closed 5-minute candles for the watch-only signal book. */
  m5?: LearnBar[];
  signalNow?: number;
}

export interface LearnRead {
  structure: Structure;
  levels: LevelLine[];
  lesson: Lesson;
  study: Study;
  signals: SignalBook;
}

export interface JobState {
  build: StructureBuild;
  prev: LearnBar[];
}

function sameBar(a: LearnBar, b: LearnBar): boolean {
  return a.time === b.time && a.high === b.high && a.low === b.low && a.close === b.close;
}

function isAppend(prev: readonly LearnBar[], next: readonly LearnBar[]): boolean {
  if (prev.length > next.length) return false;
  for (let i = 0; i < prev.length; i += 1) {
    if (!sameBar(prev[i]!, next[i]!)) return false;
  }
  return true;
}

export function runJob(job: LearnJob, state: JobState | null): { read: LearnRead; state: JobState } {
  const reusable = state != null && state.build.left === job.left && state.build.right === job.right && isAppend(state.prev, job.bars);
  const build = extendStructure(reusable ? state.build : emptyBuild(job.left, job.right), job.bars);
  const structure: Structure = { swings: build.swings, events: build.events, trend: trendOf(build, job.rangeSwings) };
  const levels = readLevels(job.bars);
  const lesson = readLesson({ bars: job.bars, structure, levels, timeframe: job.timeframe, symbol: job.symbol });
  let low = Number.POSITIVE_INFINITY;
  let high = Number.NEGATIVE_INFINITY;
  let last = 0;
  for (const bar of job.bars) {
    low = Math.min(low, bar.low);
    high = Math.max(high, bar.high);
    last = bar.close;
  }
  if (!Number.isFinite(low)) {
    low = 0;
    high = 0;
  }
  const read: LearnRead = {
    structure,
    levels,
    lesson,
    study: buildStudy({
      bars: job.bars,
      structure,
      lesson,
      now: job.now ?? job.bars[job.bars.length - 1]?.time ?? 0,
      low,
      high,
      last,
    }),
    signals: readSignalBook(
      job.m5 ?? (job.timeframe === "5m" ? job.bars : []),
      job.signalNow ?? job.now ?? 0,
      job.symbol ?? "NIFTY50",
      { left: job.left, right: job.right },
    ),
  };
  return { read, state: { build, prev: job.bars } };
}
