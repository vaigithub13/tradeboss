import { create } from "zustand";

import { indicatorKey, scopeKey } from "../indicators/cache";
import { CLIENT_ONLY_INDICATORS, type IndicatorType } from "../indicators/catalog";
import { LiveClient, type LinkState, type SocketLike } from "../live/client";
import type { BarMessage, LiveStatus, LiveView } from "../live/protocol";
import { useChartStore, useSensexChartStore } from "./chartStore";
import { useIndicatorStore, useSensexIndicatorStore } from "./indicatorStore";

interface LiveState {
  link: LinkState;
  status: LiveStatus | null;
  /** local time (ms) the last status message arrived */
  statusAt: number | null;
  /** local time (ms) the last bar message was applied */
  lastBarAt: number | null;
  /** last server-side problem message (unknown symbol, ...) */
  lastError: string | null;
  start: () => void;
  stop: () => void;
  setView: (view: LiveView | null) => void;
  /** Nifty + Sensex together: one WS message with `views: [...]`. */
  setViews: (views: LiveView[]) => void;
  /** Close the socket so the client reconnects and re-sends the current view. */
  reconnect: () => void;
}

let client: LiveClient | null = null;

export const liveUrl = (loc: Pick<Location, "protocol" | "host">): string =>
  `${loc.protocol === "https:" ? "wss" : "ws"}://${loc.host}/api/live/ws`;

/** Fold one `bar` message into the matching chart and indicator stores (candles first). */
export function applyBar(msg: BarMessage): void {
  useLiveStore.setState({ lastBarAt: Date.now() });
  const targets = [
    { chart: useChartStore, indicators: useIndicatorStore },
    { chart: useSensexChartStore, indicators: useSensexIndicatorStore },
  ] as const;
  for (const { chart, indicators } of targets) {
    const changed = chart.getState().applyLive(msg.symbol, msg.timeframe, msg.candles);
    if (!changed) continue;
    const loaded = chart.getState().loaded;
    if (!loaded || !msg.times || !msg.indicators?.length) return;
    const byKey: Record<string, Record<string, (number | null)[]>> = {};
    for (const ind of msg.indicators) byKey[ind.id] = ind.outputs; // ids are indicator keys (see liveView)
    indicators.getState().applyLiveTail(scopeKey(loaded.symbol, loaded.timeframe, loaded.sessions), msg.times, byKey);
    return;
  }
}

export const useLiveStore = create<LiveState>((set) => ({
  link: "closed",
  status: null,
  statusAt: null,
  lastBarAt: null,
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
        onReload: (symbol) => {
          void useChartStore.getState().refreshSymbols(symbol);
          void useSensexChartStore.getState().refreshSymbols(symbol);
        },
        onError: (message) => {
          console.error("[live]", message);
          set({ lastError: message });
        },
      },
    );
    client.start();
  },

  stop: () => {
    client?.stop();
    client = null;
    set({ link: "closed", status: null, statusAt: null, lastBarAt: null });
  },

  setView: (view) => client?.setView(view),

  setViews: (views) => client?.setViews(views),

  reconnect: () => {
    client?.reconnect();
  },
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
    // Client-only overlays (e.g. first_hour_box) must never reach the backend — an unknown
    // type rejects the whole view and stops live bar pushes after reconnect.
    if (CLIENT_ONLY_INDICATORS.has(it.type as IndicatorType)) continue;
    const id = indicatorKey(it.type as never, it.params as never);
    if (seen.has(id)) continue;
    seen.add(id);
    indicators.push({ id, type: it.type, params: it.params });
  }
  return { symbol: loaded.symbol, timeframe: loaded.timeframe, sessions: [...loaded.sessions], indicators };
}
