import type { SessionType } from "../api/client";
import { Popover } from "../components/Popover";
import { useChartStore } from "../store/chartStore";

const OPTIONS: { type: SessionType; label: string; hint: string }[] = [
  { type: "weekend_full", label: "Weekend (full-length)", hint: "Full-length Saturday/Sunday sessions, e.g. Budget day, 2024-01-20" },
  { type: "special_short", label: "Special short sessions", hint: "Short or broken sessions, e.g. 2024-03-02" },
  { type: "muhurat", label: "Muhurat (Diwali)", hint: "Diwali Muhurat trading, anchored to its own start time" },
];

export function SessionsMenu() {
  const sessions = useChartStore((s) => s.sessions);
  const toggleSession = useChartStore((s) => s.toggleSession);

  return (
    <Popover label="Sessions ▾" title="Which session types are shown" panelClassName="w-72">
      <div className="flex flex-col gap-1">
        <label className="flex items-center gap-2 px-1 py-1 text-xs text-white/40">
          <input type="checkbox" checked disabled /> Regular sessions (always)
        </label>
        {OPTIONS.map((o) => (
          <label
            key={o.type}
            title={o.hint}
            className="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-xs text-white/80 hover:bg-white/5"
          >
            <input
              type="checkbox"
              checked={sessions.includes(o.type)}
              onChange={() => void toggleSession(o.type)}
              className="accent-sky-600"
            />
            {o.label}
          </label>
        ))}
      </div>
    </Popover>
  );
}
