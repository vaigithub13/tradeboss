/** A written read, then the next six candles. Education only. Not an order. */

export const PRACTICE_BARS = 6;
export const PRACTICE_TAGS = ["trend pullback", "level break", "range fade", "other"] as const;
export type PracticeTag = (typeof PRACTICE_TAGS)[number];

export interface PracticeRow {
  id: string;
  day: string;
  symbol: string;
  direction: "up" | "down";
  reason: string;
  tag: PracticeTag;
  level: number | null;
  directionHit: boolean | null;
  levelHit: boolean | null;
}

export function scorePractice(
  signalClose: number,
  upcoming: readonly { high: number; low: number; close: number }[],
  direction: "up" | "down",
  level?: number | null,
): { ready: boolean; directionHit: boolean | null; levelHit: boolean | null } {
  const window = upcoming.slice(0, PRACTICE_BARS);
  if (window.length < PRACTICE_BARS) return { ready: false, directionHit: null, levelHit: null };
  const end = window[window.length - 1]!.close;
  const directionHit = direction === "up" ? end > signalClose : end < signalClose;
  const levelHit = level == null ? null : window.some((bar) => bar.low <= level && level <= bar.high);
  return { ready: true, directionHit, levelHit };
}

export function tagAccuracy(rows: readonly PracticeRow[]): { tag: PracticeTag; reads: number; correct: number }[] {
  return PRACTICE_TAGS.map((tag) => {
    const own = rows.filter((row) => row.tag === tag && row.directionHit != null);
    return { tag, reads: own.length, correct: own.filter((row) => row.directionHit).length };
  });
}

const KEY = "tradeboss-learn-practice";

export async function loadPractice(): Promise<PracticeRow[]> {
  try {
    const res = await fetch("/api/learn/practice");
    if (!res.ok) return fromBrowser();
    const body = (await res.json()) as { reads?: PracticeRow[] };
    const reads = body.reads ?? [];
    localStorage.setItem(KEY, JSON.stringify(reads));
    return reads;
  } catch {
    return fromBrowser();
  }
}

export async function savePractice(reads: PracticeRow[]): Promise<"file" | "browser"> {
  localStorage.setItem(KEY, JSON.stringify(reads));
  try {
    const res = await fetch("/api/learn/practice", {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ reads }),
    });
    return res.ok ? "file" : "browser";
  } catch {
    return "browser";
  }
}

function fromBrowser(): PracticeRow[] {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as PracticeRow[]) : [];
  } catch {
    return [];
  }
}
