import { useEffect } from "react";

import { useAlertStore } from "../store/alertStore";
import { usePaperStore } from "../store/paperStore";
import { alertNotification, deskAlerts, type AlertMemory, type PaperAlert } from "./alerts";
import { playAlertSound } from "./sounds";

const POLL_MS = 3000;

function notify(a: PaperAlert): void {
  try {
    if (typeof Notification === "undefined" || Notification.permission !== "granted") return;
    const { title, body } = alertNotification(a);
    new Notification(title, { body, silent: true }); // the panel plays its own sound
  } catch {
    // notifications blocked or unsupported: the sound still plays
  }
}

/** Polls the paper desk every 3 s (also while the panel is closed) and sounds and notifies each new live entry and
 * exit. Mounted once, in App. */
export function usePaperAlerts(): void {
  const refresh = usePaperStore((s) => s.refresh);
  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => void refresh(), POLL_MS);
    return () => window.clearInterval(id);
  }, [refresh]);

  useEffect(() => {
    let memory: AlertMemory = {};
    return usePaperStore.subscribe((state, prev) => {
      if (!state.status || state.status === prev.status) return;
      const settings = useAlertStore.getState();
      const out = deskAlerts(memory, state.status.slots, (slot) => settings.slots[slot] !== false);
      memory = out.memory;
      for (const a of out.alerts) {
        if (settings.soundOn) playAlertSound(a.kind, settings.volume);
        notify(a);
      }
    });
  }, []);
}
