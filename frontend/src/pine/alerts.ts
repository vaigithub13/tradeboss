import { playPineAlertSound } from "../paper/sounds";
import { useAlertStore } from "../store/alertStore";
import { usePineLiveStore, type PineAlertRow } from "../store/pineLiveStore";
import { alertDedupeKey, type PineSignal } from "./signals";

/** In-memory dedupe so the same bar never alerts twice in a session. */
const fired = new Set<string>();

export function resetPineAlertMemory(): void {
  fired.clear();
}

export function pineNotification(row: PineAlertRow): { title: string; body: string } {
  return {
    title: `${row.side} · ${row.scriptName}`,
    body: `${row.symbol} ${row.timeframe} @ ${row.price.toFixed(2)} — ${row.text}`,
  };
}

function notifyBrowser(row: PineAlertRow): void {
  try {
    if (typeof Notification === "undefined" || Notification.permission !== "granted") return;
    const { title, body } = pineNotification(row);
    new Notification(title, { body, silent: true });
  } catch {
    // denied / unsupported — sound + log still work
  }
}

/** Ask once from a user gesture ("Run live"). Denied is fine. */
export async function requestPineNotificationPermission(): Promise<NotificationPermission | "unsupported"> {
  try {
    if (typeof Notification === "undefined") return "unsupported";
    if (Notification.permission === "granted" || Notification.permission === "denied") {
      return Notification.permission;
    }
    return await Notification.requestPermission();
  } catch {
    return "unsupported";
  }
}

export function firePineAlerts(
  scriptId: string,
  _scriptName: string,
  muted: boolean,
  rows: PineAlertRow[],
  opts: { playSound: boolean },
): PineAlertRow[] {
  const fresh: PineAlertRow[] = [];
  for (const row of rows) {
    const key = alertDedupeKey(scriptId, row);
    if (fired.has(key)) continue;
    fired.add(key);
    fresh.push(row);
  }
  if (fresh.length === 0) return [];

  usePineLiveStore.getState().appendAlerts(fresh);

  const volume = useAlertStore.getState().volume;
  const soundOn = usePineLiveStore.getState().soundOn;
  for (const row of fresh) {
    if (!muted && opts.playSound && soundOn) {
      const kind = row.side === "BUY" ? "buy" : row.side === "SELL" ? "sell" : "exit";
      playPineAlertSound(kind, volume);
    }
    if (!muted) notifyBrowser(row);
  }
  return fresh;
}

export function signalsToAlertRows(
  script: { id: string; name: string; symbol: string; timeframe: string },
  signals: readonly PineSignal[],
): PineAlertRow[] {
  return signals.map((s) => ({
    id: `${script.id}-${s.time}-${s.side}-${s.text}`,
    time: s.time,
    scriptId: script.id,
    scriptName: script.name,
    symbol: script.symbol,
    timeframe: script.timeframe,
    side: s.side,
    text: s.text,
    price: s.price,
    day: (() => {
      const d = new Date((s.time + 19800) * 1000);
      return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}-${String(d.getUTCDate()).padStart(2, "0")}`;
    })(),
  }));
}
