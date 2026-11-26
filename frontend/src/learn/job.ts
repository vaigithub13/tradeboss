/** One incremental pass. The worker keeps `state` between candle closes. */

import { readLesson, type Lesson } from "./lesson";
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
}

export interface LearnRead {
  structure: Structure;
  levels: LevelLine[];
  lesson: Lesson;
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
  const read: LearnRead = {
    structure,
    levels,
    lesson: readLesson({ bars: job.bars, structure, levels, timeframe: job.timeframe, symbol: job.symbol }),
  };
  return { read, state: { build, prev: job.bars } };
}
