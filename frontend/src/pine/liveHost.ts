/**
 * Main-thread host: closed-bar events → Web Worker → markers + alerts.
 * Chart series.update always wins; work is async after the emit.
 */
import { useEffect } from "react";

import { niftyReplaySession, sensexReplaySession } from "../replay/session";
import { usePineLiveStore, type PineMarkerSignal } from "../store/pineLiveStore";
import { firePineAlerts, signalsToAlertRows } from "./alerts";
import { subscribeClosedBars, type ChartScopeId, type ClosedBarEvent } from "./closedBars";
import type { WorkerIn, WorkerOut } from "./live.worker";
import type { RunRequest } from "./runEngine";

let worker: Worker | null = null;
const inflight = new Set<string>();

function getWorker(): Worker {
  if (!worker) {
    worker = new Worker(new URL("./live.worker.ts", import.meta.url), { type: "module" });
    worker.onmessage = (ev: MessageEvent<WorkerOut>) => {
      const msg = ev.data;
      if (msg.type === "result") {
        inflight.delete(msg.result.scriptId);
        applyResult(msg.result.scriptId, msg.result);
      } else if (msg.type === "error") {
        inflight.delete(msg.scriptId);
        usePineLiveStore.getState().setError(msg.scriptId, msg.message);
      }
    };
    worker.onerror = (err) => {
      console.error("[pine] worker", err);
    };
  }
  return worker;
}

function applyResult(
  scriptId: string,
  result: { signals: import("./signals").PineSignal[]; notices: string[]; error?: string; msPerBar: number },
): void {
  const store = usePineLiveStore.getState();
  const script = store.scripts.find((s) => s.id === scriptId);
  if (!script) return;

  if (result.error) {
    store.setError(scriptId, result.error);
    return;
  }
  if (result.notices.length) store.setNotices(scriptId, result.notices);

  const labeled: PineMarkerSignal[] = result.signals.map((s) => ({
    ...s,
    scriptId,
    label: script.name,
  }));
  store.replaceMarkers(scriptId, labeled);

  const replayActive =
    (script.scope === "nifty" && niftyReplaySession.get() != null) ||
    (script.scope === "sensex" && sensexReplaySession.get() != null);
  const playSound = !replayActive || store.replaySounds;
  const rows = signalsToAlertRows(script, result.signals);
  firePineAlerts(scriptId, script.name, script.muted, rows, { playSound });
}

function closedCandles(ev: ClosedBarEvent): RunRequest["candles"] {
  // Live: drop forming tip. History/replay full set: still drop tip if a newer bar exists after closedTime.
  if (ev.history) {
    // On first load, treat all but the last as closed (last may still be forming).
    return ev.candles.length > 1 ? ev.candles.slice(0, -1) : [...ev.candles];
  }
  // Live append: include through the bar that closed (exclude new tip).
  return ev.candles.filter((c) => c.time <= ev.closedTime);
}

function postRun(scriptId: string, request: RunRequest): void {
  if (inflight.has(scriptId)) return;
  inflight.add(scriptId);
  const w = getWorker();
  const msg: WorkerIn = { type: "run", request };
  w.postMessage(msg);
}

function onClosedBar(ev: ClosedBarEvent): void {
  const { scripts } = usePineLiveStore.getState();
  for (const script of scripts) {
    if (!script.enabled) continue;
    if (script.scope !== ev.scope) continue;
    if (script.symbol !== ev.symbol || script.timeframe !== ev.timeframe) continue;
    const candles = closedCandles(ev);
    if (candles.length === 0) continue;
    postRun(script.id, {
      scriptId: script.id,
      source: script.source,
      ticker: script.symbol,
      timeframe: script.timeframe,
      candles,
    });
  }
}

/** Mount once in App — wires closed-bar bus to the worker. */
export function usePineLiveHost(): void {
  useEffect(() => {
    const unsub = subscribeClosedBars(onClosedBar);
    return () => {
      unsub();
    };
  }, []);
}

/** Kick a full history run for one script (after Run live). */
export function runScriptHistory(
  scriptId: string,
  scope: ChartScopeId,
  symbol: string,
  timeframe: string,
  candles: readonly RunRequest["candles"][number][],
): void {
  const closed = candles.length > 1 ? candles.slice(0, -1) : [...candles];
  const script = usePineLiveStore.getState().scripts.find((s) => s.id === scriptId);
  if (!script) return;
  postRun(scriptId, {
    scriptId,
    source: script.source,
    ticker: symbol,
    timeframe,
    candles: closed,
  });
  void scope;
}
