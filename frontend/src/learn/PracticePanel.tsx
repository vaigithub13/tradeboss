import { useEffect, useState } from "react";

import type { LearnBar } from "./structure";
import {
  PRACTICE_BARS,
  PRACTICE_TAGS,
  loadPractice,
  savePractice,
  scorePractice,
  tagAccuracy,
  type PracticeRow,
  type PracticeTag,
} from "./practice";

export function PracticePanel({
  bars,
  hiddenFrom,
  symbol,
  day,
  onHold,
  onRows,
}: {
  bars: LearnBar[];
  hiddenFrom: number | null;
  symbol: string;
  day: string;
  onHold: (hold: boolean) => void;
  onRows: (rows: PracticeRow[]) => void;
}) {
  const [rows, setRows] = useState<PracticeRow[]>([]);
  const [direction, setDirection] = useState<"up" | "down">("up");
  const [reason, setReason] = useState("");
  const [tag, setTag] = useState<PracticeTag>("trend pullback");
  const [level, setLevel] = useState("");
  const [saved, setSaved] = useState(false);
  const [revealed, setRevealed] = useState(false);
  const [where, setWhere] = useState<"file" | "browser" | null>(null);
  const cut = hiddenFrom ?? Math.max(0, bars.length - PRACTICE_BARS);
  const signalClose = bars[cut - 1]?.close ?? null;
  const upcoming = bars.slice(cut);

  useEffect(() => {
    let gone = false;
    void loadPractice().then((file) => {
      if (gone) return;
      setRows(file);
      onRows(file);
    });
    return () => { gone = true; };
    // The load runs once. onRows is the latest setter from the parent.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const tally = tagAccuracy(rows);
  const score = revealed && signalClose != null
    ? scorePractice(signalClose, upcoming, direction, level.trim() === "" ? null : Number(level))
    : null;

  return (
    <div data-testid="learn-practice">
      <p>Write the read before you see the next {PRACTICE_BARS} candles. The chart is holding them back.</p>
      <label className="row">Direction
        <select aria-label="Direction" value={direction} onChange={(e) => setDirection(e.target.value as "up" | "down")}>
          <option value="up">Up</option>
          <option value="down">Down</option>
        </select>
      </label>
      <label className="row">Reason
        <input aria-label="Reason" value={reason} onChange={(e) => setReason(e.target.value)} />
      </label>
      <label className="row">Tag
        <select aria-label="Tag" value={tag} onChange={(e) => setTag(e.target.value as PracticeTag)}>
          {PRACTICE_TAGS.map((item) => <option key={item} value={item}>{item}</option>)}
        </select>
      </label>
      <label className="row">Level
        <input aria-label="Level" value={level} onChange={(e) => setLevel(e.target.value)} />
      </label>
      <p>
        <button
          type="button"
          className="ghost"
          data-testid="learn-practice-save"
          disabled={reason.trim() === "" || signalClose == null}
          onClick={() => {
            setSaved(true);
          }}
        >Save the read</button>
        {" "}
        <button
          type="button"
          className="ghost"
          data-testid="learn-practice-reveal"
          disabled={!saved}
          onClick={() => {
            if (signalClose == null) return;
            const result = scorePractice(signalClose, upcoming, direction, level.trim() === "" ? null : Number(level));
            const row: PracticeRow = {
              id: `${symbol}-${day}-${rows.length}`,
              day,
              symbol,
              direction,
              reason: reason.trim(),
              tag,
              level: level.trim() === "" ? null : Number(level),
              directionHit: result.directionHit,
              levelHit: result.levelHit,
            };
            const next = [...rows, row];
            setRows(next);
            onRows(next);
            setRevealed(true);
            onHold(false);
            void savePractice(next).then(setWhere);
          }}
        >See what happened next</button>
      </p>
      {score?.ready && (
        <p data-testid="learn-practice-score">
          {score.directionHit ? "Direction hit." : "Direction missed."}
          {score.levelHit == null ? "" : score.levelHit ? " Level traded." : " Level missed."}
        </p>
      )}
      {score && !score.ready && <p>Not enough candles after the read to score it.</p>}
      <h3>Accuracy</h3>
      <ul>
        {tally.map((row) => (
          <li key={row.tag}>{row.tag}: {row.reads === 0 ? "no reads" : `${row.correct}/${row.reads}`}</li>
        ))}
      </ul>
      {where && <p className="muted">{where === "file" ? "Saved in data/learn/practice." : "Saved in this browser. The practice file was unreachable."}</p>}
    </div>
  );
}
