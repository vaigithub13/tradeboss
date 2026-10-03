import { useEffect, useState } from "react";

import { badgeView, type Tone } from "../live/badge";
import { useLiveStore } from "../store/liveStore";

const TONE: Record<Tone, { dot: string; text: string }> = {
  ok: { dot: "bg-emerald-400", text: "text-emerald-300" },
  warn: { dot: "bg-yellow-400", text: "text-yellow-300" },
  bad: { dot: "bg-red-500", text: "text-red-400" },
  idle: { dot: "bg-white/30", text: "text-white/50" },
};

/** live / stale (Ns since last tick) / reconnecting / market closed */
export function LiveBadge() {
  const link = useLiveStore((s) => s.link);
  const status = useLiveStore((s) => s.status);
  const statusAt = useLiveStore((s) => s.statusAt);
  const [now, setNow] = useState(() => Date.now());

  // keep the "Ns since last tick" counter moving between status messages
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const view = badgeView(status, link, statusAt, now);
  const tone = TONE[view.tone];
  return (
    <div
      className="flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3 py-1"
      data-testid="live-badge"
      title={status ? `feed: ${status.connection ?? status.state}, market ${status.market}${status.recording ? ", recording" : ""}` : "live feed"}
    >
      <span className={`h-2 w-2 rounded-full ${tone.dot}`} />
      <span className={`text-xs font-medium ${tone.text}`} data-testid="live-badge-label">
        {view.label}
      </span>
    </div>
  );
}
