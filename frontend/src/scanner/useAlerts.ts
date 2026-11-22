import { useEffect, useRef } from "react";

import { fetchScannerStatus, type ScannerAlert } from "../api/scanner";
import { playAlertSound, unlockAudio } from "../paper/sounds";

const POLL_MS = 5000;

function notify(alert: ScannerAlert): void {
  try {
    if (typeof Notification === "undefined" || Notification.permission !== "granted") return;
    const title =
      alert.kind === "entry" || alert.kind === "good_signal"
        ? "Stock Scanner"
        : alert.kind === "exit" || alert.kind === "flat"
          ? "Stock Scanner exit"
          : "Stock Scanner";
    new Notification(title, { body: alert.message, silent: true });
  } catch {
    // the sound still plays when notifications are blocked
  }
}

/** Polls slot 8 alerts even when the scanner tab is closed. The first poll is silent. */
export function useScannerAlerts(): void {
  const seen = useRef<Set<string> | null>(null);
  useEffect(() => {
    let stop = false;
    const tick = async () => {
      try {
        const status = await fetchScannerStatus();
        if (stop) return;
        const ids = status.alerts.map((alert) => alert.id);
        if (seen.current === null) {
          seen.current = new Set(ids);
          return;
        }
        for (const alert of status.alerts) {
          if (seen.current.has(alert.id)) continue;
          seen.current.add(alert.id);
          const sound = alert.kind === "exit" || alert.kind === "flat" ? "exit" : "entry";
          playAlertSound(sound, 0.8);
          notify(alert);
        }
      } catch {
        // backend down: try again on the next tick
      }
    };
    void tick();
    const id = window.setInterval(() => void tick(), POLL_MS);
    return () => {
      stop = true;
      window.clearInterval(id);
    };
  }, []);
}

export function enableScannerSound(): void {
  void unlockAudio();
  if (typeof Notification !== "undefined" && Notification.permission === "default") {
    void Notification.requestPermission();
  }
}
