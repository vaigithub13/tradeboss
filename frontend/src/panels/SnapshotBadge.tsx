import { useEffect, useState } from "react";

import { fetchSnapshotStatus, type SnapshotStatus } from "../api/client";
import { snapshotBadge } from "./upstoxUi";

/** how often the snapshot age is re-read (a new day can start while the app is open) */
const SNAPSHOT_POLL_MS = 10 * 60_000;

/** Header warning: shown only when the newest instrument snapshot is older than one trading day. */
export function SnapshotBadge() {
  const [status, setStatus] = useState<SnapshotStatus | null>(null);

  useEffect(() => {
    const load = () => void fetchSnapshotStatus().then(setStatus, () => setStatus(null));
    load();
    const id = window.setInterval(load, SNAPSHOT_POLL_MS);
    return () => window.clearInterval(id);
  }, []);

  const ui = snapshotBadge(status);
  if (!ui) return null;
  return (
    <span
      title={ui.title}
      data-testid="snapshot-badge"
      className="flex items-center gap-2 rounded-full border border-yellow-400/30 bg-yellow-400/10 px-3 py-1"
    >
      <span className={`h-2 w-2 rounded-full ${ui.dot}`} />
      <span className={`text-xs font-medium ${ui.text}`}>{ui.label}</span>
    </span>
  );
}
