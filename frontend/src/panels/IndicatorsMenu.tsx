import { useMemo, useState } from "react";

import { Popover } from "../components/Popover";
import { hasVolume } from "../chart/volume";
import {
  CATALOG,
  INDICATOR_TYPES,
  SOURCES,
  indicatorName,
  parseParam,
  unavailableReason,
  type IndicatorInstance,
  type IndicatorType,
  type ParamDef,
} from "../indicators/catalog";
import { useChartStore } from "../store/chartStore";
import { useIndicatorStore } from "../store/indicatorStore";

function ParamField({
  def,
  value,
  onCommit,
}: {
  def: ParamDef;
  value: number | string;
  onCommit: (value: number | string) => string | null;
}) {
  const [draft, setDraft] = useState(String(value));
  const [error, setError] = useState<string | null>(null);

  if (def.kind === "source") {
    return (
      <label className="flex items-center justify-between gap-2 text-xs text-white/70">
        {def.label}
        <select
          value={String(value)}
          onChange={(e) => setError(onCommit(e.target.value))}
          className="w-28 rounded border border-white/15 bg-[#0b0e14] px-1.5 py-1 text-xs text-white"
        >
          {SOURCES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </label>
    );
  }

  const change = (text: string) => {
    setDraft(text);
    const parsed = parseParam(def, text);
    if (parsed === null) {
      const range = def.kind === "int" ? `whole number ${def.min}–${def.max}` : `number > ${def.min} and ≤ ${def.max}`;
      setError(`${def.label}: ${range}`);
      return;
    }
    setError(onCommit(parsed));
  };

  return (
    <div className="flex flex-col gap-0.5">
      <label className="flex items-center justify-between gap-2 text-xs text-white/70">
        {def.label}
        <input
          type="number"
          value={draft}
          step={def.step ?? 1}
          onChange={(e) => change(e.target.value)}
          aria-invalid={error !== null}
          className={`w-28 rounded border bg-[#0b0e14] px-1.5 py-1 text-xs text-white ${
            error ? "border-red-500" : "border-white/15"
          }`}
        />
      </label>
      {error && <span className="text-right text-[11px] text-red-400">{error}</span>}
    </div>
  );
}

function Row({ item, reason }: { item: IndicatorInstance; reason: string | null }) {
  const [open, setOpen] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const update = useIndicatorStore((s) => s.update);
  const remove = useIndicatorStore((s) => s.remove);
  const duplicate = useIndicatorStore((s) => s.duplicate);
  const def = CATALOG[item.type];

  return (
    <div className="rounded border border-white/10" data-testid={`indicator-row-${item.id}`}>
      <div className="flex items-center gap-1.5 px-1.5 py-1">
        <input
          type="checkbox"
          checked={item.visible}
          onChange={(e) => update(item.id, { visible: e.target.checked })}
          title={item.visible ? "Hide" : "Show"}
          className="accent-sky-600"
        />
        <span className="flex gap-0.5">
          {def.colors.map((c) => (
            <span
              key={c.key}
              className="h-2.5 w-2.5 rounded-sm"
              style={{ background: item.colors[c.key] }}
              title={c.label}
            />
          ))}
        </span>
        <span className={`min-w-0 flex-1 truncate text-xs ${reason ? "text-white/35" : "text-white/85"}`} title={reason ?? undefined}>
          {indicatorName(item)}
        </span>
        <button type="button" onClick={() => setOpen((o) => !o)} title="Settings" className="rounded px-1.5 text-xs text-white/60 hover:bg-white/10">
          {open ? "▾" : "⚙"}
        </button>
        <button type="button" onClick={() => duplicate(item.id)} title="Add another copy" className="rounded px-1.5 text-xs text-white/60 hover:bg-white/10">
          ⧉
        </button>
        <button type="button" onClick={() => remove(item.id)} title="Remove" className="rounded px-1.5 text-xs text-white/60 hover:bg-red-500/30">
          ✕
        </button>
      </div>
      {reason && <div className="px-2 pb-1 text-[11px] text-amber-400/80">{reason}</div>}
      {open && (
        <div className="flex flex-col gap-1.5 border-t border-white/10 px-2 py-2">
          {def.params.map((p) => (
            <ParamField
              key={p.key}
              def={p}
              value={item.params[p.key] ?? p.default}
              onCommit={(v) => {
                const err = update(item.id, { params: { ...item.params, [p.key]: v } });
                setProblem(err);
                return err;
              }}
            />
          ))}
          {def.colors.map((c) => (
            <label key={c.key} className="flex items-center justify-between gap-2 text-xs text-white/70">
              {c.label}
              <input
                type="color"
                value={item.colors[c.key] ?? c.default}
                onChange={(e) => update(item.id, { colors: { [c.key]: e.target.value } })}
                className="h-6 w-10 cursor-pointer rounded border border-white/15 bg-transparent"
              />
            </label>
          ))}
          {problem && <div className="text-[11px] text-red-400">{problem}</div>}
        </div>
      )}
    </div>
  );
}

export function IndicatorsMenu() {
  const items = useIndicatorStore((s) => s.items);
  const add = useIndicatorStore((s) => s.add);
  const status = useIndicatorStore((s) => s.status);
  const error = useIndicatorStore((s) => s.error);
  const loaded = useChartStore((s) => s.loaded);
  const candles = useChartStore((s) => s.candles);

  const ctx = useMemo(
    () => (loaded ? { timeframe: loaded.timeframe, hasVolume: hasVolume(candles) } : null),
    [loaded, candles],
  );
  const reasonFor = (type: IndicatorType): string | null => (ctx ? unavailableReason(type, ctx) : null);

  return (
    <Popover
      label={
        <>
          Indicators{items.length > 0 && <span className="ml-1 text-white/40">{items.length}</span>} ▾
        </>
      }
      title="Add, remove and edit indicators"
      panelClassName="w-80 max-h-[75vh] overflow-y-auto"
    >
      <div className="flex flex-col gap-2">
        <div className="grid grid-cols-2 gap-1" data-testid="indicator-add">
          {INDICATOR_TYPES.map((type) => {
            const reason = reasonFor(type);
            return (
              <button
                key={type}
                type="button"
                aria-disabled={reason !== null}
                title={reason ?? `Add ${CATALOG[type].label}`}
                onClick={() => {
                  if (!reason) add(type);
                }}
                className={`rounded border px-2 py-1 text-left text-xs ${
                  reason
                    ? "cursor-not-allowed border-white/5 text-white/30"
                    : "border-white/15 text-white/85 hover:bg-white/10"
                }`}
              >
                + {CATALOG[type].label}
                <span className="ml-1 text-[10px] text-white/30">{CATALOG[type].pane === "price" ? "price" : "pane"}</span>
              </button>
            );
          })}
        </div>
        {reasonFor("vwap") && <div className="text-[11px] text-amber-400/80">VWAP disabled: {reasonFor("vwap")}</div>}

        {items.length === 0 ? (
          <div className="px-1 py-2 text-xs text-white/40">No indicators yet. Add as many copies as you like (e.g. EMA 20 + EMA 50).</div>
        ) : (
          <div className="flex flex-col gap-1.5">
            {items.map((item) => (
              <Row key={item.id} item={item} reason={reasonFor(item.type)} />
            ))}
          </div>
        )}
        {status === "loading" && <div className="text-[11px] text-white/40">calculating…</div>}
        {error && <div className="text-[11px] text-red-400">{error}</div>}
      </div>
    </Popover>
  );
}
