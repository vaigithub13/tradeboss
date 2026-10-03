import { create } from "zustand";

import { indicatorKey, scopeKey } from "../indicators/cache";
import { LiveClient, type LinkState, type SocketLike } from "../live/client";
import type { BarMessage, LiveStatus, LiveView } from "../live/protocol";
import { useChartStore } from "./chartStore";
import { useIndicatorStore } from "./indicatorStore";

interface LiveState {
  link: LinkState;
  status: LiveStatus | null;
  /** local time (ms) the last status message arrived */
  statusAt: number | null;
  /** last server-side problem message (unknown symbol, ...) */
  lastError: string | null;
  start: () => void;
  stop: () => void;
  setView: (view: LiveView | null) => void;
}

let client: LiveClient | null = null;

export const liveUrl = (loc: Pick<Location, "protocol" | "host">): string =>
  `${loc.protocol === "https:" ? "wss" : "ws"}://${loc.host}/api/live/ws`;

/** Fold one `bar` message into the chart and indicator stores. */
export function applyBar(msg: BarMessage): void {
  const chart = useChartStore.getState();
  const changed = chart.applyLive(msg.symbol, msg.timeframe, msg.candles);
  const loaded = useChartStore.getState().loaded;
  if (!changed || !loaded || !msg.times || !msg.indicators?.length) return;
  const byKey: Record<string, Record<string, (number | null)[]>> = {};
  for (const ind of msg.indicators) byKey[ind.id] = ind.outputs; // ids are indicator keys (see liveView)
  useIndicatorStore.getState().applyLiveTail(scopeKey(loaded.symbol, loaded.timeframe, loaded.sessions), msg.times, byKey);
}

export const useLiveStore = create<LiveState>((set) => ({
  link: "closed",
  status: null,
  statusAt: null,
  lastError: null,

  start: () => {
    if (client) return;
    client = new LiveClient(
      {
        url: liveUrl(window.location),
        makeSocket: (url) => new WebSocket(url) as unknown as SocketLike,
        timers: {
          setTimeout: (fn, ms) => window.setTimeout(fn, ms),
          clearTimeout: (id) => window.clearTimeout(id as number),
          now: () => Date.now(),
        },
      },
      {
        onLink: (link) => set({ link }),
        onStatus: (status, at) => set({ status, statusAt: at }),
        onBar: applyBar,
        onReload: (symbol) => void useChartStore.getState().refreshSymbols(symbol),
        onError: (message) => set({ lastError: message }),
      },
    );
    client.start();
  },

  stop: () => {
    client?.stop();
    client = null;
    set({ link: "closed", status: null, statusAt: null });
  },

  setView: (view) => client?.setView(view),
}));

/** The view to tell the server about: what is loaded + the visible indicators (deduplicated by key). */
export function liveView(
  loaded: { symbol: string; timeframe: string; sessions: readonly string[] } | null,
  items: readonly { type: string; params: Record<string, unknown>; visible: boolean }[],
): LiveView | null {
  if (!loaded) return null;
  const seen = new Set<string>();
  const indicators: LiveView["indicators"] = [];
  for (const it of items) {
    if (!it.visible) continue;
    const id = indicatorKey(it.type as never, it.params as never);
    if (seen.has(id)) continue;
    seen.add(id);
    indicators.push({ id, type: it.type, params: it.params });
  }
  return { symbol: loaded.symbol, timeframe: loaded.timeframe, sessions: [...loaded.sessions], indicators };
}
