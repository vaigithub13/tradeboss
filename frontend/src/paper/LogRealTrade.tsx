import { useEffect, useState } from "react";

import { upsertRealTrade } from "../api/paper";
import type { ReportRow } from "../report/present";
import {
  REAL_EXIT_REASONS,
  fromDatetimeLocalValue,
  paperTradeKey,
  realNet,
  toDatetimeLocalValue,
  type RealTradeLog,
} from "./real";

/** Form under a paper trade row: log the fill you took by hand (does not change paper). */
export function LogRealTrade({
  slot,
  day,
  row,
  existing,
  onSaved,
}: {
  slot: string;
  day: string;
  row: ReportRow;
  existing?: RealTradeLog | null;
  onSaved?: (trade: RealTradeLog) => void;
}) {
  const [open, setOpen] = useState(false);
  const [entryTime, setEntryTime] = useState("");
  const [entryPrice, setEntryPrice] = useState("");
  const [exitTime, setExitTime] = useState("");
  const [exitPrice, setExitPrice] = useState("");
  const [exitReason, setExitReason] = useState<string>("target");
  const [charges, setCharges] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    if (existing) {
      setEntryTime(toDatetimeLocalValue(existing.entry_time));
      setEntryPrice(String(existing.entry_price));
      setExitTime(toDatetimeLocalValue(existing.exit_time));
      setExitPrice(String(existing.exit_price));
      setExitReason(existing.exit_reason);
      setCharges(String(existing.charges));
    } else {
      setEntryTime(toDatetimeLocalValue(row.fill_time));
      setEntryPrice(String(row.entry_premium));
      setExitTime(row.exit_time != null ? toDatetimeLocalValue(row.exit_time) : "");
      setExitPrice(row.exit_premium != null ? String(row.exit_premium) : "");
      setExitReason(row.exit_reason ?? "other");
      setCharges("");
    }
    setError(null);
  }, [open, existing, row]);

  const units = row.units;
  const entryP = Number(entryPrice);
  const exitP = Number(exitPrice);
  const ch = Number(charges);
  const preview =
    Number.isFinite(entryP) && Number.isFinite(exitP) && Number.isFinite(ch) && charges !== ""
      ? realNet(entryP, exitP, units, ch)
      : null;

  const save = async () => {
    const et = fromDatetimeLocalValue(entryTime);
    const xt = fromDatetimeLocalValue(exitTime);
    if (et == null || xt == null) {
      setError("Entry and exit need a date and time (IST).");
      return;
    }
    if (!Number.isFinite(entryP) || !Number.isFinite(exitP) || !Number.isFinite(ch) || charges === "") {
      setError("Entry price, exit price and charges are required.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const { trade } = await upsertRealTrade({
        slot,
        day,
        fill_time: row.fill_time,
        contract: row.contract,
        entry_time: et,
        entry_price: entryP,
        exit_time: xt,
        exit_price: exitP,
        exit_reason: exitReason,
        charges: ch,
        units,
      });
      onSaved?.(trade);
      setOpen(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="mt-1 rounded border border-dashed border-white/15 p-2 text-[11px]" aria-label="Log real trade">
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className="rounded border border-white/15 px-2 py-0.5" onClick={() => setOpen(!open)}>
          {open ? "Cancel" : existing ? "Edit real trade" : "Log real trade"}
        </button>
        {existing && !open && (
          <span className="text-white/50">
            Real net {existing.net.toFixed(2)} · key {paperTradeKey(slot, day, row.fill_time)}
          </span>
        )}
      </div>
      {open && (
        <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-3">
          <label className="flex flex-col gap-0.5">
            Entry time (IST)
            <input type="datetime-local" step={1} className="rounded border border-white/15 bg-transparent px-1 py-0.5"
              value={entryTime} onChange={(e) => setEntryTime(e.target.value)} aria-label="Real entry time" />
          </label>
          <label className="flex flex-col gap-0.5">
            Entry price
            <input type="number" step="0.05" className="rounded border border-white/15 bg-transparent px-1 py-0.5"
              value={entryPrice} onChange={(e) => setEntryPrice(e.target.value)} aria-label="Real entry price" />
          </label>
          <label className="flex flex-col gap-0.5">
            Exit time (IST)
            <input type="datetime-local" step={1} className="rounded border border-white/15 bg-transparent px-1 py-0.5"
              value={exitTime} onChange={(e) => setExitTime(e.target.value)} aria-label="Real exit time" />
          </label>
          <label className="flex flex-col gap-0.5">
            Exit price
            <input type="number" step="0.05" className="rounded border border-white/15 bg-transparent px-1 py-0.5"
              value={exitPrice} onChange={(e) => setExitPrice(e.target.value)} aria-label="Real exit price" />
          </label>
          <label className="flex flex-col gap-0.5">
            Exit reason
            <select className="rounded border border-white/15 bg-transparent px-1 py-0.5" value={exitReason}
              onChange={(e) => setExitReason(e.target.value)} aria-label="Real exit reason">
              {REAL_EXIT_REASONS.map((r) => (
                <option key={r} value={r} className="bg-[#0b0f14]">{r}</option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-0.5">
            Charges (contract note)
            <input type="number" step="0.01" min={0} className="rounded border border-white/15 bg-transparent px-1 py-0.5"
              value={charges} onChange={(e) => setCharges(e.target.value)} aria-label="Real trade charges" />
          </label>
          <div className="col-span-full flex flex-wrap items-center gap-2">
            <button type="button" className="rounded border border-emerald-500/40 px-2 py-0.5 text-emerald-200 disabled:opacity-40"
              disabled={saving} onClick={() => void save()}>
              {saving ? "Saving…" : "Save real trade"}
            </button>
            {preview != null && <span className="text-white/50">Net preview {preview.toFixed(2)} ({units} units)</span>}
          </div>
          {error && <div className="col-span-full text-red-400">{error}</div>}
        </div>
      )}
    </div>
  );
}
