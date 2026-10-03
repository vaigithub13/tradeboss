import { useEffect } from "react";

import { useUpstoxStore } from "../store/upstoxStore";
import { tokenBadge } from "./upstoxUi";

/** how often the (server-cached) data-token status is re-read */
const TOKEN_POLL_MS = 60_000;

/** Header badge: "data token valid / invalid / expired". Click = re-check now. */
export function DataTokenBadge() {
  const token = useUpstoxStore((s) => s.token);
  const failed = useUpstoxStore((s) => s.tokenCheckFailed);
  const pollToken = useUpstoxStore((s) => s.pollToken);

  useEffect(() => {
    void pollToken();
    const id = window.setInterval(() => void pollToken(), TOKEN_POLL_MS);
    return () => window.clearInterval(id);
  }, [pollToken]);

  const ui = tokenBadge(token, failed);
  return (
    <button
      type="button"
      onClick={() => void pollToken(true)}
      title={`${ui.title} (click to re-check)`}
      data-testid="data-token-badge"
      className="flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3 py-1 hover:bg-white/10"
    >
      <span className={`h-2 w-2 rounded-full ${ui.dot}`} />
      <span className={`text-xs font-medium ${ui.text}`}>{ui.label}</span>
    </button>
  );
}
