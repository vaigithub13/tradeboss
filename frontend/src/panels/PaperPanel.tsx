import { useMemo, useState } from "react";

import { ACTIVE_PAPER_SLOTS } from "../paper/slots";
import { CompactLiveCard } from "../paper/CompactLiveCard";
import { ExpiryStraddleCard } from "../paper/ExpiryStraddleCard";
import { todaySummaryFromSlots } from "../paper/dashboard";
import { formatRupees, SLOT_DEFAULTS, startParams } from "../paper/present";
import { playAlertSound, unlockAudio } from "../paper/sounds";
import { useAlertStore } from "../store/alertStore";
import { usePaperStore } from "../store/paperStore";

const PANEL_WIDTH = 360;

/** Compact Paper side panel: header, today summary, live cards, link to dashboard. */
export function PaperPanel({ onOpenDashboard }: { onOpenDashboard?: () => void }) {
  const status = usePaperStore((s) => s.status);
  const pollError = usePaperStore((s) => s.errors[""] ?? null);
  const start = usePaperStore((s) => s.start);
  const stop = usePaperStore((s) => s.stop);
  const disabled = status?.state === "disabled";

  const activeStatuses = useMemo(() => {
    const bySlot = new Map((status?.slots ?? []).map((s) => [s.slot, s]));
    return ACTIVE_PAPER_SLOTS.map((slot) => bySlot.get(slot) ?? null);
  }, [status]);

  const summary = useMemo(
    () => todaySummaryFromSlots(status?.slots?.filter((s) => ACTIVE_PAPER_SLOTS.includes(s.slot as never)) ?? []),
    [status],
  );

  const expiry = useMemo(
    () => (status?.slots ?? []).find((s) => s.slot === "9") ?? null,
    [status],
  );

  const anyRunning = activeStatuses.some((s) => s?.state === "running" || s?.state === "starting");

  const masterToggle = async () => {
    if (anyRunning) {
      for (const s of activeStatuses) {
        if (s && (s.state === "running" || s.state === "starting")) await stop(s.slot);
      }
      return;
    }
    for (const slot of ACTIVE_PAPER_SLOTS) {
      const def = SLOT_DEFAULTS[slot];
      if (!def) continue;
      const cur = activeStatuses.find((s) => s?.slot === slot);
      if (cur?.state === "running" || cur?.state === "starting") continue;
      await start(slot, def.strategy, startParams(def.params ? JSON.parse(def.params) : {}, def.exit));
    }
  };

  return (
    <aside
      className="relative flex h-full min-w-0 shrink-0 flex-col gap-3 overflow-x-hidden overflow-y-auto border-l border-white/10 bg-[#0b0f14] p-3 text-xs text-white/80"
      style={{ width: PANEL_WIDTH, maxWidth: PANEL_WIDTH }}
      data-testid="paper-side-panel"
      aria-label="Paper side panel"
    >
      <header className="flex min-w-0 flex-wrap items-center gap-2">
        <h2 className="shrink-0 text-sm font-semibold text-white">Paper</h2>
        <button
          type="button"
          className={`rounded border px-2 py-0.5 text-[11px] ${
            anyRunning ? "border-emerald-400/50 bg-emerald-500/15 text-emerald-200" : "border-white/15 text-white/70"
          }`}
          disabled={disabled}
          onClick={() => void masterToggle()}
          data-testid="paper-master-toggle"
          title={anyRunning ? "Stop all active slots" : "Start all active slots"}
        >
          {anyRunning ? "ON" : "OFF"}
        </button>
        <AlertHeaderBits />
      </header>

      {pollError && (
        <div className="rounded border border-red-500/30 bg-red-950/40 px-2 py-1 text-red-300" data-testid="paper-panel-error">
          {pollError.includes("fetch") || pollError.includes("Network") || pollError.includes("Failed")
            ? "backend offline"
            : pollError}
        </div>
      )}
      {disabled && (
        <div className="text-yellow-300">Live feed off — paper cannot run.</div>
      )}

      <div
        className="flex min-w-0 items-baseline justify-between gap-2 rounded border border-white/10 bg-white/[0.03] px-2 py-1.5"
        data-testid="paper-today-summary"
      >
        <div className="min-w-0">
          <div className="text-[10px] uppercase tracking-wide text-white/40">Today</div>
          <div className={`truncate text-base font-semibold tabular-nums ${summary.net < 0 ? "text-red-400" : "text-emerald-400"}`}>
            {formatRupees(summary.net)}
          </div>
        </div>
        <div className="shrink-0 text-right text-white/55 tabular-nums">
          <div>{summary.trades} trades</div>
          <div>
            {summary.wins}W / {summary.losses}L
            {summary.winPct != null ? ` · ${summary.winPct}%` : ""}
          </div>
        </div>
      </div>

      <div className="flex min-w-0 flex-col gap-2">
        {ACTIVE_PAPER_SLOTS.map((slot, i) => (
          <CompactLiveCard key={slot} slot={slot} status={activeStatuses[i] ?? null} />
        ))}
        <ExpiryStraddleCard status={expiry} />
      </div>

      <button
        type="button"
        className="mt-auto rounded border border-sky-400/40 bg-sky-500/10 px-3 py-2 text-left text-sky-100 hover:bg-sky-500/20"
        onClick={onOpenDashboard}
        data-testid="open-paper-dashboard"
      >
        Open Paper dashboard →
      </button>
    </aside>
  );
}

function AlertHeaderBits() {
  const soundOn = useAlertStore((s) => s.soundOn);
  const volume = useAlertStore((s) => s.volume);
  const setSoundOn = useAlertStore((s) => s.setSoundOn);
  const setVolume = useAlertStore((s) => s.setVolume);
  const [busy, setBusy] = useState(false);

  const enable = async () => {
    setBusy(true);
    try {
      setSoundOn(await unlockAudio());
      if (typeof Notification !== "undefined" && Notification.permission === "default") {
        await Notification.requestPermission();
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="ml-auto flex min-w-0 flex-wrap items-center justify-end gap-1.5">
      {!soundOn ? (
        <button
          type="button"
          className="rounded border border-amber-400/50 px-1.5 py-0.5 text-[11px] text-amber-200"
          disabled={busy}
          onClick={() => void enable()}
        >
          Enable sounds
        </button>
      ) : (
        <span className="text-[11px] text-emerald-300">sounds on</span>
      )}
      <label className="flex items-center gap-1 text-[11px] text-white/50">
        <span className="sr-only">Volume</span>
        <input
          type="range"
          min={0}
          max={100}
          aria-label="Alert volume"
          className="w-16"
          value={Math.round(volume * 100)}
          onChange={(e) => setVolume(Number(e.target.value) / 100)}
        />
      </label>
      {soundOn && (
        <button
          type="button"
          className="rounded border border-white/15 px-1 py-0.5 text-[10px] text-white/50"
          title="Quick test (full test buttons are on the dashboard Details)"
          onClick={() => playAlertSound("entry", volume)}
        >
          ♪
        </button>
      )}
    </div>
  );
}
