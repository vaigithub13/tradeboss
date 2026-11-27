/** Chart notes. The file under data/learn/notes/ is the copy of record; the browser keeps one if that file is unreachable. */

import type { StudyNote } from "./study";

const KEY = "tradeboss-learn-notes";

export interface NotesFile {
  notes: StudyNote[];
  reads: Record<string, string>;
}

const EMPTY: NotesFile = { notes: [], reads: {} };

function fromBrowser(): NotesFile {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return EMPTY;
    const parsed = JSON.parse(raw) as NotesFile;
    return { notes: parsed.notes ?? [], reads: parsed.reads ?? {} };
  } catch {
    return EMPTY;
  }
}

export async function loadNotes(): Promise<NotesFile> {
  try {
    const res = await fetch("/api/learn/notes");
    if (!res.ok) return fromBrowser();
    const body = (await res.json()) as NotesFile;
    const file = { notes: body.notes ?? [], reads: body.reads ?? {} };
    localStorage.setItem(KEY, JSON.stringify(file));
    return file;
  } catch {
    return fromBrowser();
  }
}

export async function saveNotes(file: NotesFile): Promise<"file" | "browser"> {
  localStorage.setItem(KEY, JSON.stringify(file));
  try {
    const res = await fetch("/api/learn/notes", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(file),
    });
    return res.ok ? "file" : "browser";
  } catch {
    return "browser";
  }
}
