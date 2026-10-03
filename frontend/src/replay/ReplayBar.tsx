import { useEffect, useState } from "react";

import { NEXT_TRADE_LABEL, istCursor, type ReplaySpeed, type ReplayUnit } from "./cursor";
import { useReplayStore } from "./store";

const SPEEDS: ReplaySpeed[] = [1, 2, 5, 10];

export function ReplayBar() {
  const open = useReplayStore((s) => s.open);
  const active = useReplayStore((s) => s.active);
  const playing = useReplayStore((s) => s.playing);
  const speed = useReplayStore((s) => s.speed);
  const unit = useReplayStore((s) => s.unit);
  const practice = useReplayStore((s) => s.practice);
  const toggle = useReplayStore((s) => s.toggle);
  const start = useReplayStore((s) => s.start);
  const play = useReplayStore((s) => s.play);
  const pause = useReplayStore((s) => s.pause);
  const step = useReplayStore((s) => s.step);
  const setSpeed = useReplayStore((s) => s.setSpeed);
  const setUnit = useReplayStore((s) => s.setUnit);
  const jumpTrade = useReplayStore((s) => s.jumpTrade);
  const exit = useReplayStore((s) => s.exit);
  const [day, setDay] = useState("2026-06-15");
  const [time, setTime] = useState("09:15");

  return (
    <div className="flex flex-wrap items-center gap-2">
      <button
        type="button"
        className="rounded border border-white/15 px-2 py-1 text-xs text-white/80"
        onClick={toggle}
      >
        Replay
      </button>
      {open && (
        <div className="flex flex-wrap items-center gap-2 rounded border border-white/10 bg-white/5 px-2 py-1 text-xs text-white/80">
          <input aria-label="Replay date" type="date" className="bg-transparent" value={day} onChange={(e) => setDay(e.target.value)} />
          <input aria-label="Replay time" type="time" className="bg-transparent" value={time} onChange={(e) => setTime(e.target.value)} />
          <button type="button" className="rounded bg-sky-700 px-2 py-1 text-white" onClick={() => start(istCursor(day, time))}>
            Start
          </button>
          <button type="button" aria-label={playing ? "Pause" : "Play"} disabled={!active} onClick={playing ? pause : play}>
            {playing ? "Pause" : "Play"}
          </button>
          <button type="button" aria-label="Step" disabled={!active} onClick={step}>
            Step
          </button>
          <label className="flex items-center gap-1">
            Unit
            <select aria-label="Replay unit" value={unit} onChange={(e) => setUnit(e.target.value as ReplayUnit)}>
              <option value="chart">chart bar</option>
              <option value="1m">1 minute</option>
            </select>
          </label>
          <label className="flex items-center gap-1">
            Speed
            <select aria-label="Replay speed" value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
              {SPEEDS.map((value) => (
                <option key={value} value={value}>{value}/s</option>
              ))}
            </select>
          </label>
          <button
            type="button"
            disabled={!active || practice}
            title="Review shortcut. Disabled in practice mode."
            onClick={jumpTrade}
          >
            {NEXT_TRADE_LABEL}
          </button>
          <button type="button" disabled={!active} onClick={exit}>
            Exit
          </button>
        </div>
      )}
    </div>
  );
}

/** One speed-step per second while play is on. Step itself stays paused. */
export function useReplayClock(): void {
  const playing = useReplayStore((s) => s.playing);
  const tick = useReplayStore((s) => s.tick);
  useEffect(() => {
    if (!playing) return;
    const id = window.setInterval(() => tick(), 1000);
    return () => window.clearInterval(id);
  }, [playing, tick]);
}
