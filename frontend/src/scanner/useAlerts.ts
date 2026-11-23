import type { ScannerAlert } from "../api/scanner";
import { playAlertSound, unlockAudio } from "../paper/sounds";

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

const seen = new Set<string>();
let primed = false;

/** Called from the scanner tab's snapshot. No request of its own, and nothing runs while the tab is hidden. */
export function noteScannerAlerts(alerts: ScannerAlert[]): void {
  if (!primed) {
    for (const alert of alerts) seen.add(alert.id);
    primed = true;
    return;
  }
  for (const alert of alerts) {
    if (seen.has(alert.id)) continue;
    seen.add(alert.id);
    const sound = alert.kind === "exit" || alert.kind === "flat" ? "exit" : "entry";
    playAlertSound(sound, 0.8);
    notify(alert);
  }
}

export function enableScannerSound(): void {
  void unlockAudio();
  if (typeof Notification !== "undefined" && Notification.permission === "default") {
    void Notification.requestPermission();
  }
}
