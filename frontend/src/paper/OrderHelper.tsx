import { useState } from "react";

import { orderHelperFromOpen, type OrderHelper as Helper, type PaperOpenPosition } from "./real";

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

function CopyField({ label, value }: { label: string; value: string }) {
  const [ok, setOk] = useState(false);
  return (
    <div className="flex items-center gap-2">
      <span className="w-28 shrink-0 text-white/50">{label}</span>
      <code className="min-w-0 flex-1 truncate rounded bg-white/5 px-1.5 py-0.5 tabular-nums" title={value}>{value}</code>
      <button
        type="button"
        className="shrink-0 rounded border border-white/15 px-2 py-0.5"
        aria-label={`Copy ${label}`}
        onClick={() => void copyText(value).then((v) => { setOk(v); window.setTimeout(() => setOk(false), 1200); })}
      >
        {ok ? "Copied" : "Copy"}
      </button>
    </div>
  );
}

/** When a slot (3 or 5) has an open paper option with premium levels: fields to place the real order by hand.
 * `trailing`: the slot's stop trails (slot 5), so the SL row changes while the trade is open. */
export function OrderHelperBox({ open, slot = "3", trailing }: {
  open: PaperOpenPosition | null | undefined; slot?: string; trailing?: string;
}) {
  const helper: Helper | null = orderHelperFromOpen(open);
  if (!helper) return null;

  return (
    <section className="flex flex-col gap-1.5 rounded border border-sky-500/30 bg-sky-950/30 p-2" aria-label="Order helper">
      <div className="font-semibold text-sky-200">Order helper (slot {slot} signal): BUY {helper.kind ?? ""}</div>
      <table className="w-full text-left tabular-nums" aria-label="Signal to buy">
        <tbody>
          <tr><td className="w-28 text-white/50">Buy</td><td className="font-semibold">{helper.kind ?? "-"}</td></tr>
          <tr><td className="text-white/50">Strike</td><td className="font-semibold">{helper.strike ?? "-"}</td></tr>
          <tr><td className="text-white/50">Paper entry</td><td>{helper.entry_premium.toFixed(2)}</td></tr>
          <tr><td className="text-white/50">Lots</td><td>{helper.lots} × {helper.lot_size} = {helper.quantity}</td></tr>
          <tr><td className="text-white/50">Stop loss now</td><td className="text-red-300">{helper.stop_trigger.toFixed(2)}</td></tr>
          <tr>
            <td className="text-white/50">Target</td>
            <td>{helper.target_limit == null ? "none: exit when paper exits (Supertrend / stop)" : helper.target_limit.toFixed(2)}</td>
          </tr>
        </tbody>
      </table>
      {trailing && <p className="text-amber-200/80">{trailing}</p>}
      <p className="text-white/50">
        Paper bought the option. Place the same contract by hand; SL is a sell-stop{helper.target_limit == null ? "" : ", target a sell limit"}.
      </p>
      <CopyField label="Contract" value={helper.contract} />
      <CopyField label="Quantity" value={String(helper.quantity)} />
      <CopyField label="SL trigger" value={helper.stop_trigger.toFixed(2)} />
      <CopyField label="SL limit" value={helper.stop_limit.toFixed(2)} />
      {helper.target_limit != null && <CopyField label="Target limit" value={helper.target_limit.toFixed(2)} />}
    </section>
  );
}
