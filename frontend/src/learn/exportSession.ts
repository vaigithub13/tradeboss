/** Markdown a coach can read. The picture is saved beside it. */

import type { Lesson } from "./lesson";
import type { PracticeRow } from "./practice";
import { tagAccuracy } from "./practice";
import type { WatchSignal } from "./signals";
import type { StudyNote } from "./study";

export interface ExportInput {
  day: string;
  symbol: string;
  banners: readonly string[];
  amd: Lesson["sessions"];
  events: readonly { title: string; verified: boolean; source: string }[];
  signals: readonly WatchSignal[];
  notes: readonly StudyNote[];
  read: string;
  practice: readonly PracticeRow[];
}

export function sessionMarkdown(input: ExportInput): string {
  const lines = [
    `# Learn session ${input.day}`,
    "",
    `${input.symbol}. Watch only. This note is for a coach. It is not a trade.`,
    "",
    "## Now banner",
    "",
    ...(input.banners.length ? input.banners.map((line) => `- ${line}`) : ["- No banner yet."]),
    "",
    "## AMD",
    "",
  ];
  const session = input.amd.find((item) => item.day === input.day) ?? input.amd.at(-1);
  if (!session) lines.push("No AMD read on the closed candles.");
  else {
    lines.push(session.accumulation ? `Accumulation ${session.accumulation.low}–${session.accumulation.high}.` : "Accumulation not set.");
    lines.push(session.manipulation ? `Manipulation: sweep of ${session.manipulation.name} to ${session.manipulation.extreme}.` : "No manipulation.");
    lines.push(session.distribution ? `Distribution ${session.distribution.direction}.` : "No distribution.");
    if (session.note) lines.push(session.note);
  }
  lines.push("", "## Events", "");
  if (input.events.length === 0) lines.push("No listed event.");
  for (const item of input.events) {
    lines.push(`- ${item.title}${item.verified ? "" : " (unverified)"} — ${item.source}`);
  }
  lines.push("", "## Signals", "");
  if (input.signals.length === 0) lines.push("No signal in the loaded window.");
  for (const signal of input.signals) {
    lines.push(`- ${signal.setup} ${signal.side} ${signal.status} entry ${signal.entry} SL ${signal.sl} TP ${signal.tp} — ${signal.why}`);
  }
  lines.push("", "## My notes", "");
  if (input.notes.length === 0) lines.push("No note.");
  for (const note of input.notes) lines.push(`- ${note.price}: ${note.text}`);
  lines.push("", "## My read of today", "", input.read.trim() || "No written read.");
  lines.push("", "## Practice", "");
  const tally = tagAccuracy(input.practice.filter((row) => row.day === input.day));
  for (const row of tally) {
    if (row.reads === 0) continue;
    lines.push(`- ${row.tag}: ${row.correct}/${row.reads}`);
  }
  if (tally.every((row) => row.reads === 0)) lines.push("No practice score today.");
  lines.push("");
  return lines.join("\n");
}
