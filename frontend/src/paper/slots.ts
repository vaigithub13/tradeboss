/** Active vs retired paper slots (UI only — does not change backend configs). */

/** Live cards / dashboard: Price Channel and DD Quantman. Log XZ slots 1 and 3 are retired. */
export const ACTIVE_PAPER_SLOTS = ["2", "4", "5", "6"] as const;
export const RETIRED_PAPER_SLOTS = ["1", "3"] as const;

export type ActivePaperSlot = (typeof ACTIVE_PAPER_SLOTS)[number];
export type RetiredPaperSlot = (typeof RETIRED_PAPER_SLOTS)[number];

export const SLOT_LABELS: Record<string, string> = {
  "1": "Log XZ (retired)",
  "2": "Price Channel",
  "3": "Log XZ 1:2 (retired)",
  "4": "Price Channel 1:2",
  "5": "DD Quantman CE",
  "6": "DD Quantman PE",
};

export function isActivePaperSlot(slot: string): boolean {
  return (ACTIVE_PAPER_SLOTS as readonly string[]).includes(slot);
}

export function isRetiredPaperSlot(slot: string): boolean {
  return (RETIRED_PAPER_SLOTS as readonly string[]).includes(slot);
}

export function slotLabel(slot: string): string {
  return SLOT_LABELS[slot] ?? `Slot ${slot}`;
}

export type SlotStatusPill = "waiting" | "in trade" | "session ended" | "off" | "starting";

export function slotStatusPill(status: {
  state?: string | null;
  ended_by?: string | null;
  position?: unknown;
} | null | undefined): SlotStatusPill {
  if (!status || status.state === "stopped" || status.state === "disabled") {
    if (status?.ended_by) return "session ended";
    return "off";
  }
  if (status.state === "starting") return "starting";
  if (status.state === "running") {
    if (status.position) return "in trade";
    return "waiting";
  }
  return "off";
}

export const STATUS_PILL_CLASS: Record<SlotStatusPill, string> = {
  waiting: "bg-sky-500/20 text-sky-200",
  "in trade": "bg-amber-500/25 text-amber-200",
  "session ended": "bg-white/10 text-white/55",
  off: "bg-white/5 text-white/40",
  starting: "bg-violet-500/20 text-violet-200",
};
