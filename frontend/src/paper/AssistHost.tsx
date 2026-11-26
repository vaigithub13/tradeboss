import { useCallback, useEffect, useRef, useState } from "react";

import {
  fetchAssist,
  fetchAssistTicket,
  postAssistClosed,
  postAssistPractice,
  postAssistPracticeRead,
  postAssistPracticeReveal,
  postAssistSkipped,
  postAssistTaken,
  postAssistTrail,
  saveAssistSettings,
  type PaperStatus,
} from "../api/paper";
import { useChartStore } from "../store/chartStore";
import { usePaperStore } from "../store/paperStore";
import { fromDatetimeLocalValue, toDatetimeLocalValue } from "./real";
import { formatRupees } from "./present";
import { audioReady, playAssistSound, unlockAudio } from "./sounds";
import {
  assistEvents,
  exitLabel,
  istDate,
  istMinutes,
  paperKey,
  PRACTICE_TAGS,
  premiumBracket,
  reminderDue,
  scorePractice,
  SKIP_REASONS,
  storyDue,
  storyGroups,
  type AssistEvent,
  type AssistSettings,
  type AssistSlot,
  type AssistState,
  type AssistTicket,
  type ChartCandle,
  type Guard,
  type PracticeTag,
  type SkipReason,
  type TrailNotice,
} from "./assist";
import { AssistChart } from "./AssistChart";

function asAssist(slot: PaperStatus): AssistSlot {
  return slot as unknown as AssistSlot;
}

function notify(title: string, body: string): void {
  try {
    if (typeof Notification === "undefined" || Notification.permission !== "granted") return;
    new Notification(title, { body, silent: true });
  } catch {
    // the sound still plays when the browser blocks notifications
  }
}

export function AssistBanner({ guard, enabled, onToggleSettings, onStory, onPractice }: {
  guard: Guard | null;
  enabled: boolean;
  onToggleSettings: () => void;
  onStory?: () => void;
  onPractice?: () => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-white/10 bg-[#0c1218] px-3 py-1 text-xs" data-testid="assist-banner">
      <span className="font-semibold text-white">Real assist</span>
      {!enabled && <span className="text-white/50">Popups are off.</span>}
      {guard && (
        <span className="tabular-nums text-white/80">
          Today {guard.trades_today}/{guard.max_trades} · P&L {formatRupees(guard.pnl_today)}
          {guard.start_balance != null && guard.equity != null && ` · equity ${formatRupees(guard.equity)} of ${formatRupees(guard.start_balance)}`}
        </span>
      )}
      {guard?.at_trade_cap && <span className="text-amber-200">Trade cap reached.</span>}
      {guard?.stop_for_today && (
        <span className="font-semibold text-red-300" data-testid="assist-stop">
          STOP for today — real P&L is at −₹{guard.day_loss.toFixed(0)}.
        </span>
      )}
      {guard?.pause && (
        <span className="font-semibold text-red-300" data-testid="assist-pause">
          Pause — the account is down ₹{guard.account_pause.toFixed(0)} from the start balance.
        </span>
      )}
      {guard?.needs_start_balance && <span className="text-white/40">Enter a start balance to track the account.</span>}
      {onStory && (
        <button type="button" className="rounded border border-white/15 px-2 py-0.5 text-white/70" data-testid="assist-story-open" onClick={onStory}>
          Story
        </button>
      )}
      {onPractice && (
        <button type="button" className="rounded border border-white/15 px-2 py-0.5 text-white/70" data-testid="assist-practice-open" onClick={onPractice}>
          Practice
        </button>
      )}
      <button type="button" className="ml-auto rounded border border-white/15 px-2 py-0.5 text-white/70" onClick={onToggleSettings}>
        Settings
      </button>
    </div>
  );
}

export function AssistEntryDialog({
  event,
  ticket,
  busy,
  error,
  onTook,
  onSkip,
}: {
  event: AssistEvent;
  ticket: AssistTicket | null;
  busy: boolean;
  error: string | null;
  onTook: (fillPrice: number, entryTime: number, slSet: boolean, tpSet: boolean) => void;
  onSkip: (reason: SkipReason) => void;
}) {
  const suggested = ticket?.suggested_limit ?? event.fillPrice ?? null;
  const [price, setPrice] = useState(suggested != null ? String(suggested) : "");
  const [when, setWhen] = useState(toDatetimeLocalValue(Math.floor(Date.now() / 1000)));
  const [slSet, setSlSet] = useState(true);
  const [tpSet, setTpSet] = useState(true);
  const [reason, setReason] = useState<SkipReason>("late");
  const fill = Number(price);
  const trailing = Boolean(ticket?.trailing);
  const stopFrac = ticket?.stop_frac ?? (trailing ? 0.1 : 0.2);
  const targetFrac = trailing ? null : ticket?.target_frac ?? 0.4;
  const bracket = !trailing && Number.isFinite(fill) && fill > 0 && stopFrac != null && targetFrac != null
    ? premiumBracket(fill, stopFrac, targetFrac)
    : null;
  const liveStop = trailing && Number.isFinite(fill) && fill > 0 ? round2(fill * (1 - stopFrac)) : null;
  const side = ticket?.option_side || (event.side === "SELL" ? "PE" : "CE");
  const market = ticket?.market ?? (event.slot === "7" ? "SENSEX" : "NIFTY");
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/70 p-4" role="dialog" aria-modal="true" aria-label="Entry ticket" data-testid="assist-entry">
      <div className="w-full max-w-lg rounded border border-sky-400/40 bg-[#0c1218] p-3 text-sm text-white">
        <h2 className="text-base font-semibold text-sky-200">{market} slot {event.slot} · BUY {side}</h2>
        <p className="mt-1 tabular-nums text-white/70">{ticket?.time ?? ""} · {ticket?.contract || event.contract}</p>
        <p className="mt-2 text-white/90" data-testid="assist-why">{ticket?.why || event.contract || "Channel values were not on the bars available."}</p>
        {ticket?.price_action?.text && <p className="mt-1 text-xs text-white/60" data-testid="assist-pa">{ticket.price_action.text}</p>}
        <div className="mt-2"><AssistChart chart={ticket?.chart} /></div>
        <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 tabular-nums text-white/80">
          <dt className="text-white/40">Bid / ask / LTP</dt>
          <dd>{fmt(ticket?.bid)} / {fmt(ticket?.ask)} / {fmt(ticket?.ltp)}</dd>
          <dt className="text-white/40">Suggested limit</dt>
          <dd>{fmt(ticket?.suggested_limit)}</dd>
          <dt className="text-white/40">Quantity</dt>
          <dd>{ticket ? `${ticket.lots} lot × ${ticket.lot_size} = ${ticket.quantity}` : "—"}</dd>
          <dt className="text-white/40">{trailing ? "SL from the limit" : "SL / TP from the limit"}</dt>
          <dd>{trailing ? `${fmt(ticket?.stop)} · exit by trailing stop` : `${fmt(ticket?.stop)} / ${fmt(ticket?.target)}`}</dd>
          <dt className="text-white/40">Max loss incl. charges</dt>
          <dd>{ticket?.max_loss == null ? "—" : `₹${ticket.max_loss.toFixed(0)}`}</dd>
        </dl>
        <p className="mt-2 text-xs text-white/40">Place this by hand. TradeBoss does not send an order.</p>
        <div className="mt-3 flex flex-col gap-2 border-t border-white/10 pt-2">
          <label className="flex items-center gap-2">Fill
            <input className="w-28 rounded border border-white/15 bg-transparent px-1 py-0.5 tabular-nums" value={price} aria-label="Fill price" onChange={(e) => setPrice(e.target.value)} />
          </label>
          <label className="flex items-center gap-2">Time
            <input type="datetime-local" step={1} className="rounded border border-white/15 bg-transparent px-1 py-0.5" value={when} aria-label="Fill time" onChange={(e) => setWhen(e.target.value)} />
          </label>
          {bracket && <p className="tabular-nums text-white/70">From this fill: SL {bracket[0].toFixed(2)} · TP {bracket[1].toFixed(2)}</p>}
          {liveStop != null && <p className="tabular-nums text-white/70" data-testid="assist-trail-note">From this fill: SL {liveStop.toFixed(2)} · exit by trailing stop</p>}
          <label className="flex items-center gap-2 text-white/70"><input type="checkbox" checked={slSet} onChange={(e) => setSlSet(e.target.checked)} /> SL set as told</label>
          {!trailing && <label className="flex items-center gap-2 text-white/70"><input type="checkbox" checked={tpSet} onChange={(e) => setTpSet(e.target.checked)} /> TP set as told</label>}
          {error && <p className="text-red-300">{error}</p>}
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className="rounded bg-sky-500/80 px-3 py-1 font-semibold disabled:opacity-40"
              data-testid="assist-took"
              disabled={busy || (trailing ? liveStop == null : !bracket)}
              onClick={() => {
                const entryTime = fromDatetimeLocalValue(when);
                if (entryTime == null || (trailing ? liveStop == null : !bracket)) return;
                onTook(fill, entryTime, slSet, trailing ? false : tpSet);
              }}
            >
              I took it
            </button>
            <select className="rounded border border-white/15 bg-[#0c1218] px-1" aria-label="Skip reason" value={reason} onChange={(e) => setReason(e.target.value as SkipReason)}>
              {SKIP_REASONS.map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
            <button type="button" className="rounded border border-white/20 px-3 py-1" data-testid="assist-skip" disabled={busy} onClick={() => onSkip(reason)}>
              Skipped
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export function AssistExitDialog({
  event,
  busy,
  error,
  onClose,
}: {
  event: AssistEvent;
  busy: boolean;
  error: string | null;
  onClose: (price: number, time: number) => void;
}) {
  const [price, setPrice] = useState(event.exitPrice != null ? String(event.exitPrice) : "");
  const [when, setWhen] = useState(toDatetimeLocalValue(event.exitAtMs ? Math.trunc(event.exitAtMs / 1000) : Math.floor(Date.now() / 1000)));
  const fill = Number(price);
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/70 p-4" role="dialog" aria-modal="true" aria-label="Exit ticket" data-testid="assist-exit">
      <div className="w-full max-w-md rounded border border-amber-400/40 bg-[#0c1218] p-3 text-sm text-white">
        <h2 className="text-base font-semibold text-amber-200">{event.slot === "7" ? "SENSEX" : "NIFTY"} slot {event.slot} exited · {event.reason ?? "exit"}</h2>
        <p className="mt-1">{event.contract}</p>
        <p className="mt-2">Check your real position. Paper sold at {fmt(event.exitPrice)}.</p>
        {error && <p className="mt-2 text-red-300">{error}</p>}
        <label className="mt-3 flex items-center gap-2">Closed at
          <input className="w-28 rounded border border-white/15 bg-transparent px-1 py-0.5 tabular-nums" aria-label="Exit price" value={price} onChange={(e) => setPrice(e.target.value)} />
        </label>
        <label className="mt-2 flex items-center gap-2">Time
          <input type="datetime-local" step={1} className="rounded border border-white/15 bg-transparent px-1 py-0.5" aria-label="Exit time" value={when} onChange={(e) => setWhen(e.target.value)} />
        </label>
        <button
          type="button"
          className="mt-3 rounded bg-amber-500/80 px-3 py-1 font-semibold disabled:opacity-40"
          data-testid="assist-closed"
          disabled={busy || !(fill > 0)}
          onClick={() => {
            const time = fromDatetimeLocalValue(when);
            if (time == null) return;
            onClose(fill, time);
          }}
        >
          Closed at
        </button>
      </div>
    </div>
  );
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

function fmt(value: number | null | undefined): string {
  return value == null ? "—" : value.toFixed(2);
}

export function AssistTrailDialog({
  notice,
  busy,
  error,
  onUpdated,
}: {
  notice: TrailNotice;
  busy: boolean;
  error: string | null;
  onUpdated: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/70 p-4" role="dialog" aria-modal="true" aria-label="Move stop" data-testid="assist-trail">
      <div className="w-full max-w-md rounded border border-sky-400/40 bg-[#0c1218] p-3 text-sm text-white">
        <h2 className="text-base font-semibold text-sky-200">{notice.market} slot {notice.slot}</h2>
        <p className="mt-1">{notice.contract}</p>
        <p className="mt-3 text-lg tabular-nums" data-testid="assist-trail-text">{notice.text}</p>
        <p className="mt-1 text-xs text-white/40">From your fill. TradeBoss does not send the change.</p>
        {error && <p className="mt-2 text-red-300">{error}</p>}
        <button type="button" className="mt-3 rounded bg-sky-500/80 px-3 py-1 font-semibold disabled:opacity-40" data-testid="assist-trail-updated" disabled={busy} onClick={onUpdated}>
          Updated
        </button>
      </div>
    </div>
  );
}

export function AssistHost() {
  const status = usePaperStore((s) => s.status);
  const [state, setState] = useState<AssistState | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [entry, setEntry] = useState<{ event: AssistEvent; ticket: AssistTicket | null } | null>(null);
  const [exitEv, setExitEv] = useState<AssistEvent | null>(null);
  const [trail, setTrail] = useState<TrailNotice | null>(null);
  const [queuedExit, setQueuedExit] = useState<AssistEvent | null>(null);
  const [reminder, setReminder] = useState(false);
  const [storyOpen, setStoryOpen] = useState(false);
  const [practiceOpen, setPracticeOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const memory = useRef<Record<string, import("./assist").AssistMemory>>({});
  const stateRef = useRef(state);
  const entryRef = useRef(entry);
  const reminderDay = useRef<string | null>(null);
  const storyDay = useRef<string | null>(null);
  const trailSeen = useRef<string | null>(null);
  const entryQueue = useRef<AssistEvent[]>([]);
  stateRef.current = state;
  entryRef.current = entry;

  const reload = useCallback(async () => {
    try {
      setState(await fetchAssist());
    } catch {
      // the banner stays on the last good state; paper slots are untouched
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload, status]);

  useEffect(() => {
    if (!status || !state) return;
    const watch = state.settings.slots?.length ? state.settings.slots : [state.settings.slot];
    for (const id of watch) {
    const slot = status.slots.find((item) => item.slot === id);
    if (!slot) continue;
    const out = assistEvents(memory.current[id], asAssist(slot), state.settings.enabled);
    memory.current[id] = out.memory;
    for (const event of out.events) {
      if (event.kind === "entry") {
        playAssistSound("entry");
        const label = event.slot === "7" ? "SENSEX" : "NIFTY";
        notify(`${label} slot ${event.slot} entry`, `${event.contract} — check the ticket`);
        if (entryRef.current || entryQueue.current.length > 0) entryQueue.current.push(event);
        else {
          entryRef.current = { event, ticket: null };
          void fetchAssistTicket(event.slot, event.decidedAtMs ?? 0)
            .then((body) => setEntry({ event, ticket: body.ticket }))
            .catch(() => setEntry({ event, ticket: null }));
        }
      } else {
        playAssistSound("exit");
        const label = event.slot === "7" ? "SENSEX" : "NIFTY";
        notify(`${label} slot ${event.slot} exit`, `${event.contract} · ${event.reason ?? "exit"}. Check your real position.`);
        const key = event.entryAtMs != null ? paperKey(event.slot, event.day ?? state.today, event.entryAtMs) : "";
        const open = stateRef.current?.open.some((row) => row.key === key) ?? false;
        const pending = entryRef.current?.event.decidedAtMs != null
          && event.entryAtMs != null
          && Math.trunc(entryRef.current.event.decidedAtMs / 1000) === Math.trunc(event.entryAtMs / 1000);
        if (pending) setQueuedExit(event);
        else if (open) setExitEv(event);
      }
    }
    }
  }, [status, state]);

  useEffect(() => {
    const next = state?.trails?.[0];
    if (!next) return;
    const id = `${next.key}|${next.paper_stop}`;
    if (trailSeen.current === id) return;
    trailSeen.current = id;
    playAssistSound("entry");
    notify(`${next.market} slot ${next.slot}`, next.text);
    setTrail(next);
  }, [state]);

  useEffect(() => {
    const id = window.setInterval(() => {
      const minute = istMinutes(new Date());
      const today = istDate(new Date());
      const open = stateRef.current?.guard.open_trades ?? 0;
      if (reminderDue(minute, open, reminderDay.current === today)) {
        reminderDay.current = today;
        setReminder(true);
        notify("15:10 reminder", "An open real trade is still on. Check the position.");
      }
      const hasStory = (stateRef.current?.running.length ?? 0) > 0;
      if (storyDue(minute) && hasStory && storyDay.current !== today) {
        storyDay.current = today;
        setStoryOpen(true);
      }
    }, 15_000);
    return () => window.clearInterval(id);
  }, []);

  async function took(fillPrice: number, entryTime: number, slSet: boolean, tpSet: boolean) {
    if (!entry?.ticket) return;
    setBusy(true);
    setError(null);
    try {
      const ticket = entry.ticket;
      await postAssistTaken({
        key: ticket.key, slot: ticket.slot, day: ticket.day, fill_time: ticket.fill_time,
        decided_at_ms: ticket.decided_at_ms, contract: ticket.contract, option_side: ticket.option_side,
        signal_side: ticket.signal_side, fill_price: fillPrice, entry_time: entryTime, sl_set: slSet, tp_set: tpSet,
        lot_size: ticket.lot_size, paper_fill: ticket.paper_fill, suggested_limit: ticket.suggested_limit,
        why: ticket.why, chart: ticket.chart, price_action: ticket.price_action ?? null,
        market: ticket.market ?? (ticket.slot === "7" ? "SENSEX" : "NIFTY"),
        trailing: Boolean(ticket.trailing),
        stop_frac: ticket.stop_frac, target_frac: ticket.trailing ? null : ticket.target_frac,
      });
      showNextEntry();
      if (queuedExit) {
        setExitEv(queuedExit);
        setQueuedExit(null);
      }
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "could not log the fill");
    } finally {
      setBusy(false);
    }
  }

  async function skipped(reason: SkipReason) {
    const ticket = entry?.ticket;
    const event = entry?.event;
    if (!ticket && !event) return;
    setBusy(true);
    setError(null);
    try {
      await postAssistSkipped({
        key: ticket?.key ?? paperKey(event!.slot, event!.day ?? state?.today ?? istDate(new Date()), (event!.decidedAtMs ?? 0)),
        slot: ticket?.slot ?? event!.slot,
        day: ticket?.day ?? event!.day ?? state?.today,
        fill_time: ticket?.fill_time ?? Math.trunc((event!.decidedAtMs ?? 0) / 1000),
        reason,
        contract: ticket?.contract ?? event!.contract,
      });
      setQueuedExit(null);
      showNextEntry();
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "could not log the skip");
    } finally {
      setBusy(false);
    }
  }

  async function closed(price: number, time: number) {
    if (!exitEv?.entryAtMs) return;
    const day = exitEv.day ?? state?.today ?? istDate(new Date());
    setBusy(true);
    setError(null);
    try {
      await postAssistClosed({
        key: paperKey(exitEv.slot, day, exitEv.entryAtMs),
        exit_price: price,
        exit_time: time,
        paper_exit_price: exitEv.exitPrice,
        paper_exit_time: exitEv.exitAtMs != null ? Math.trunc(exitEv.exitAtMs / 1000) : null,
        paper_exit_reason: exitEv.reason,
        paper_net: exitEv.net,
      });
      setExitEv(null);
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "could not log the exit");
    } finally {
      setBusy(false);
    }
  }

  function showNextEntry() {
    const next = entryQueue.current.shift();
    if (!next) {
      setEntry(null);
      return;
    }
    entryRef.current = { event: next, ticket: null };
    void fetchAssistTicket(next.slot, next.decidedAtMs ?? 0)
      .then((body) => setEntry({ event: next, ticket: body.ticket }))
      .catch(() => setEntry({ event: next, ticket: null }));
  }

  async function updatedTrail() {
    if (!trail) return;
    setBusy(true);
    setError(null);
    try {
      await postAssistTrail({
        key: trail.key, paper_stop: trail.paper_stop, real_stop: trail.real_stop,
        moved_at: trail.moved_at, updated_at: Math.floor(Date.now() / 1000),
      });
      setTrail(null);
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "could not log the stop");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <AssistBanner
        guard={state?.guard ?? null}
        enabled={state?.settings.enabled ?? false}
        onToggleSettings={() => setSettingsOpen((v) => !v)}
        onStory={state && state.running.length > 0 ? () => setStoryOpen(true) : undefined}
        onPractice={() => setPracticeOpen((open) => !open)}
      />
      {settingsOpen && state && (
        <AssistSettingsForm
          settings={state.settings}
          onClose={() => setSettingsOpen(false)}
          onSaved={(next) => { setState({ ...state, settings: next }); setSettingsOpen(false); }}
        />
      )}
      {entry && (
        <AssistEntryDialog event={entry.event} ticket={entry.ticket} busy={busy} error={error} onTook={(p, t, sl, tp) => void took(p, t, sl, tp)} onSkip={(reason) => void skipped(reason)} />
      )}
      {exitEv && (
        <AssistExitDialog event={exitEv} busy={busy} error={error} onClose={(price, time) => void closed(price, time)} />
      )}
      {trail && (
        <AssistTrailDialog notice={trail} busy={busy} error={error} onUpdated={() => void updatedTrail()} />
      )}
      {reminder && (
        <div className="fixed inset-0 z-40 flex items-start justify-center bg-black/60 p-4" role="dialog" aria-label="Square-off reminder" data-testid="assist-reminder">
          <div className="w-full max-w-md rounded border border-red-400/40 bg-[#0c1218] p-3 text-sm text-white">
            <h2 className="font-semibold text-red-200">15:10 — open real trade</h2>
            <p className="mt-2">Check the position before the close. This does not exit it.</p>
            <button type="button" className="mt-3 rounded border border-white/20 px-3 py-1" onClick={() => setReminder(false)}>Noted</button>
          </div>
        </div>
      )}
      {practiceOpen && (
        <div className="border-b border-white/10 bg-[#0c1218] px-3 py-2 text-sm text-white">
          <PracticeRead />
        </div>
      )}
      {storyOpen && state && <AssistStory state={state} onClose={() => setStoryOpen(false)} />}
    </>
  );
}

function AssistSettingsForm({ settings, onClose, onSaved }: {
  settings: AssistSettings;
  onClose: () => void;
  onSaved: (next: AssistSettings) => void;
}) {
  const [draft, setDraft] = useState(settings);
  const [error, setError] = useState<string | null>(null);
  return (
    <form
      className="flex flex-wrap items-end gap-2 border-b border-white/10 bg-[#0c1218] px-3 py-2 text-xs text-white"
      data-testid="assist-settings"
      onSubmit={(e) => {
        e.preventDefault();
        void saveAssistSettings({
          ...draft,
          start_balance: draft.start_balance,
        }).then(onSaved).catch((err: unknown) => setError(err instanceof Error ? err.message : "could not save"));
      }}
    >
      <label className="flex items-center gap-1">
        <input type="checkbox" checked={draft.enabled} onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })} />
        On
      </label>
      <label>Slots <input className="w-16 rounded border border-white/15 bg-transparent px-1" aria-label="Slots" value={(draft.slots ?? [draft.slot]).join(",")} onChange={(e) => {
        const slots = e.target.value.split(",").map((item) => item.trim()).filter(Boolean);
        setDraft({ ...draft, slots, slot: slots[0] ?? draft.slot });
      }} /></label>
      <label>Lots <input type="number" min={1} className="w-14 rounded border border-white/15 bg-transparent px-1" value={draft.lots} onChange={(e) => setDraft({ ...draft, lots: Number(e.target.value) })} /></label>
      <label>Max trades <input type="number" min={1} className="w-14 rounded border border-white/15 bg-transparent px-1" value={draft.max_trades} onChange={(e) => setDraft({ ...draft, max_trades: Number(e.target.value) })} /></label>
      <label>Day loss ₹ <input type="number" min={1} className="w-20 rounded border border-white/15 bg-transparent px-1" value={draft.day_loss} onChange={(e) => setDraft({ ...draft, day_loss: Number(e.target.value) })} /></label>
      <label>Pause ₹ <input type="number" min={1} className="w-24 rounded border border-white/15 bg-transparent px-1" value={draft.account_pause} onChange={(e) => setDraft({ ...draft, account_pause: Number(e.target.value) })} /></label>
      <label>Start balance ₹ <input type="number" min={0} className="w-28 rounded border border-white/15 bg-transparent px-1" value={draft.start_balance ?? ""} onChange={(e) => setDraft({ ...draft, start_balance: e.target.value === "" ? null : Number(e.target.value) })} /></label>
      <button type="submit" className="rounded bg-white/10 px-2 py-1">Save</button>
      <button type="button" className="px-2 py-1 text-white/50" onClick={onClose}>Close</button>
      <button
        type="button"
        className="px-2 py-1 text-sky-200"
        onClick={() => {
          void unlockAudio();
          if (typeof Notification !== "undefined" && Notification.permission === "default") void Notification.requestPermission();
        }}
      >
        {audioReady() ? "Sound on" : "Enable sound"}
      </button>
      {error && <span className="text-red-300">{error}</span>}
    </form>
  );
}

export function AssistStory({ state, onClose }: { state: AssistState; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-40 flex items-start justify-center overflow-y-auto bg-black/70 p-4" role="dialog" aria-label="Trade story" data-testid="assist-story">
      <div className="w-full max-w-3xl rounded border border-white/15 bg-[#0c1218] p-3 text-sm text-white">
        <div className="flex items-center gap-2">
          <h2 className="font-semibold">Trade story</h2>
          <button type="button" className="ml-auto rounded border border-white/20 px-2 py-0.5" onClick={onClose}>Close</button>
        </div>
        {storyGroups(state.running, state.settings.slots).map((group) => (
          <section key={group.slot} className="mt-3" data-testid={`assist-story-${group.slot}`}>
            <h3 className="font-semibold">{group.market} slot {group.slot}</h3>
            {group.rows.length === 0 && <p className="text-xs text-white/40">No real trades.</p>}
            {group.rows.length > 0 && (
              <table className="mt-2 w-full text-left text-xs tabular-nums">
                <thead className="text-white/40">
                  <tr>
                    <th>Day</th><th>Contract</th><th>Paper entry</th><th>Real entry</th><th>Slip</th>
                    <th>Paper exit</th><th>Real exit</th><th>Paper ₹</th><th>Real ₹</th><th>Rule</th>
                  </tr>
                </thead>
                <tbody>
                  {group.rows.map((row) => (
                    <tr key={row.key} className="border-t border-white/10">
                      <td>{row.day}</td>
                      <td>{row.contract}</td>
                      <td>{fmt(row.paper_entry)}</td>
                      <td>{fmt(row.real_entry)}</td>
                      <td>{fmt(row.entry_slip)}</td>
                      <td>{fmt(row.paper_exit)}</td>
                      <td>{fmt(row.real_exit)}</td>
                      <td>{row.paper_net == null ? "—" : formatRupees(row.paper_net)}</td>
                      <td>{row.real_net == null ? "—" : formatRupees(row.real_net)}</td>
                      <td>{row.rule.followed ? "yes" : "no"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {group.rows.map((row) => (
              <section key={`${row.key}-story`} className="mt-3 border-t border-white/10 pt-2">
                <p className="text-white/80">{row.why}</p>
                {row.price_action?.text && <p className="mt-1 text-xs text-white/60">{row.price_action.text}</p>}
                {row.after?.text && <p className="mt-1 text-xs text-white/60">{row.after.text}</p>}
                <p className="text-xs text-white/40">
                  {exitLabel(row.exit_reason)} · entered {row.rule.entered_on_time ? "within 1 min" : "outside 1 min"}
                  · SL {row.rule.sl_tp_set ? "set" : "not set"}
                  · trails {row.rule.trails_on_time ? "on time" : "late"}
                  · {row.rule.no_early_exit ? "no early exit" : "early exit"}
                </p>
                <AssistChart chart={row.chart} />
              </section>
            ))}
          </section>
        ))}
        <PracticeRead />
        {state.practice && state.practice.some((row) => row.reads > 0) && (
          <p className="mt-3 text-xs text-white/50">
            Practice {state.practice.filter((row) => row.reads > 0).map((row) => `${row.tag} ${row.correct}/${row.reads}`).join(" · ")}
          </p>
        )}
      </div>
    </div>
  );
}

export function PracticeRead({ bars, onPersist }: {
  bars?: ChartCandle[];
  onPersist?: (body: { id: string; direction: "up" | "down"; reason: string; tag: PracticeTag; level: number | null }) => Promise<unknown>;
}) {
  const chartBars = useChartStore((s) => s.candles);
  const source = bars ?? chartBars;
  const [direction, setDirection] = useState<"up" | "down">("up");
  const [reason, setReason] = useState("");
  const [level, setLevel] = useState("");
  const [tag, setTag] = useState<PracticeTag>("trend pullback");
  const [saved, setSaved] = useState(false);
  const [shown, setShown] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (source.length < 12) {
    return <p className="mt-3 text-xs text-white/40" data-testid="assist-practice">Not enough closed bars for a practice read.</p>;
  }
  const hidden = source.slice(-6);
  const visible = source.slice(0, -6);
  const signalClose = visible[visible.length - 1]?.close ?? 0;
  const id = String(visible[visible.length - 1]?.time ?? "");
  return (
    <section className="mt-3 border-t border-white/10 pt-2" data-testid="assist-practice">
      <p className="font-semibold">Practice read</p>
      <p className="text-xs text-white/40">Last close {signalClose.toFixed(2)}. The next 6 candles stay hidden until this is saved.</p>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
        <select aria-label="Direction" className="rounded border border-white/15 bg-[#0c1218] px-1" value={direction} onChange={(e) => setDirection(e.target.value as "up" | "down")}>
          <option value="up">up</option>
          <option value="down">down</option>
        </select>
        <input aria-label="Reason" className="w-48 rounded border border-white/15 bg-transparent px-1" value={reason} placeholder="reason" onChange={(e) => setReason(e.target.value)} />
        <input aria-label="Level" className="w-24 rounded border border-white/15 bg-transparent px-1" value={level} placeholder="level" onChange={(e) => setLevel(e.target.value)} />
        <select aria-label="Setup" className="rounded border border-white/15 bg-[#0c1218] px-1" value={tag} onChange={(e) => setTag(e.target.value as PracticeTag)}>
          {PRACTICE_TAGS.map((item) => <option key={item} value={item}>{item}</option>)}
        </select>
        <button
          type="button"
          className="rounded border border-white/20 px-2 py-0.5 disabled:opacity-40"
          data-testid="assist-practice-save"
          disabled={!reason.trim()}
          onClick={() => {
            const body = { id, direction, reason: reason.trim(), tag, level: level.trim() === "" ? null : Number(level) };
            const persist = onPersist ?? ((read) => postAssistPractice({ bars: source }).then(() => postAssistPracticeRead(read)));
            void persist(body).then(() => { setSaved(true); setError(null); }).catch((err: unknown) => setError(err instanceof Error ? err.message : "could not save the read"));
          }}
        >
          Save read
        </button>
        <button
          type="button"
          className="rounded bg-white/10 px-2 py-0.5 disabled:opacity-40"
          data-testid="assist-practice-reveal"
          disabled={!saved}
          onClick={() => {
            const score = scorePractice(signalClose, hidden, direction, level.trim() === "" ? null : Number(level));
            const line = score.directionHit ? "direction hit" : "direction miss";
            const levelLine = score.levelHit == null ? "" : score.levelHit ? ", level touched" : ", level missed";
            setShown(`${line}${levelLine}`);
            if (!onPersist) void postAssistPracticeReveal().catch(() => undefined);
          }}
        >
          See what happened next
        </button>
      </div>
      {error && <p className="mt-1 text-xs text-red-300">{error}</p>}
      {shown && <p className="mt-2 text-xs text-white/80" data-testid="assist-practice-score">{shown}</p>}
    </section>
  );
}
