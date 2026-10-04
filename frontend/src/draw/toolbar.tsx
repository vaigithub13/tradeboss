import { useEffect, useRef, useState } from "react";

import { getJson, TIMEFRAMES } from "../api/client";
import { formatCrosshairTime } from "../chart/format";
import { useChartStore } from "../store/chartStore";
import { measure, objectTreeRows, type Drawing, type DrawTool, type LineStyleName } from "./model";
import { usePositionViews } from "./positionFeed";
import { defaultPositionSettings, pointsOf, positionToolLabel, priceOf, type PositionSettings } from "./position";
import { useDrawStore, type DrawMode } from "./store";

const TOOL_LABEL: Record<DrawTool, string> = {
  trend: "Trend line",
  ray: "Ray",
  extended: "Extended line",
  horizontal: "Horizontal line",
  horizontal_ray: "Horizontal ray",
  vertical: "Vertical line",
  rectangle: "Rectangle",
  fib: "Fibonacci",
  text: "Text",
  measure: "Measure",
  long_position: "Long position",
  short_position: "Short position",
};

const TOOLS: { id: DrawMode; label: string; title: string }[] = [
  { id: "cursor", label: "↖", title: "Cursor" },
  { id: "trend", label: "╱", title: "Trend line" },
  { id: "ray", label: "→", title: "Ray" },
  { id: "extended", label: "↔", title: "Extended line" },
  { id: "horizontal", label: "―", title: "Horizontal line" },
  { id: "horizontal_ray", label: "⇢", title: "Horizontal ray" },
  { id: "vertical", label: "|", title: "Vertical line" },
  { id: "rectangle", label: "▢", title: "Rectangle" },
  { id: "fib", label: "Fib", title: "Fibonacci retracement" },
  { id: "text", label: "T", title: "Text" },
  { id: "measure", label: "↕", title: "Measure" },
  { id: "long_position", label: "L", title: "Long position" },
  { id: "short_position", label: "S", title: "Short position" },
  { id: "eraser", label: "⌫", title: "Eraser" },
];

/** Load, save, and keyboard shortcuts for the symbol on screen. */
export function DrawSync({ symbol }: { symbol: string }) {
  const drawings = useDrawStore((s) => s.drawings);
  const ready = useDrawStore((s) => s.ready);
  const loaded = useDrawStore((s) => s.symbol);

  useEffect(() => {
    void useDrawStore.getState().load(symbol);
  }, [symbol]);

  useEffect(() => {
    let cancel = false;
    void getJson<{ holidays?: string[]; weekend_sessions?: string[] }>("/api/ai/settings").then((settings) => {
      if (!cancel && settings.holidays) {
        useDrawStore.getState().setHolidays(settings.holidays, settings.weekend_sessions ?? []);
      }
    }).catch(() => {});
    return () => {
      cancel = true;
    };
  }, []);

  useEffect(() => {
    if (!ready || loaded !== symbol) return;
    const timer = setTimeout(() => {
      void useDrawStore.getState().save().catch(() => {});
    }, 300);
    return () => clearTimeout(timer);
  }, [drawings, ready, loaded, symbol]);

  useEffect(() => {
    const onKey = (ev: KeyboardEvent): void => {
      const target = ev.target;
      if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement) return;
      const store = useDrawStore.getState();
      const meta = ev.metaKey || ev.ctrlKey;
      if (meta && ev.key.toLowerCase() === "z") {
        ev.preventDefault();
        if (ev.shiftKey) store.redo();
        else store.undo();
        return;
      }
      if ((ev.key === "Delete" || ev.key === "Backspace") && store.selectedId) {
        ev.preventDefault();
        store.remove(store.selectedId);
      }
      if (ev.key === "Escape") store.setTool("cursor");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return null;
}

export function DrawToolbar() {
  const tool = useDrawStore((s) => s.tool);
  const magnet = useDrawStore((s) => s.magnet);
  const lockAll = useDrawStore((s) => s.lockAll);
  const hideAll = useDrawStore((s) => s.hideAll);
  const selectedId = useDrawStore((s) => s.selectedId);
  const drawings = useDrawStore((s) => s.drawings);
  const fileRef = useRef<HTMLInputElement>(null);
  const [treeOpen, setTreeOpen] = useState(false);
  const selected = drawings.find((item) => item.id === selectedId) ?? null;
  const timeframe = useChartStore((s) => s.timeframe);

  return (
    <aside className="relative z-20 flex w-11 shrink-0 flex-col border-r border-white/10 bg-[#0b0e14]" data-testid="draw-toolbar">
      <div className="flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto px-1 py-1">
        {TOOLS.map((item) => (
          <ToolButton key={item.id} {...item} active={tool === item.id} onClick={() => useDrawStore.getState().setTool(item.id)} />
        ))}
        <ToolButton label="◎" title={magnet ? "Magnet on" : "Magnet off"} active={magnet} onClick={() => useDrawStore.getState().setMagnet(!magnet)} testId="draw-magnet" />
        <ToolButton label="🔒" title={lockAll ? "Unlock all" : "Lock all"} active={lockAll} onClick={() => useDrawStore.getState().toggleLock()} testId="draw-lock" />
        <ToolButton label="👁" title={hideAll ? "Show drawings" : "Hide all"} active={hideAll} onClick={() => useDrawStore.getState().toggleHide()} testId="draw-hide" />
        <ToolButton label="↶" title="Undo" active={false} onClick={() => useDrawStore.getState().undo()} testId="draw-undo" />
        <ToolButton label="↷" title="Redo" active={false} onClick={() => useDrawStore.getState().redo()} testId="draw-redo" />
        <ToolButton label="☰" title="Object tree" active={treeOpen} onClick={() => setTreeOpen((open) => !open)} testId="draw-tree" />
        <ToolButton label="⇩" title="Export drawings" active={false} onClick={() => exportDrawings()} testId="draw-export" />
        <ToolButton label="⇧" title="Import drawings" active={false} onClick={() => fileRef.current?.click()} testId="draw-import" />
        <input
          ref={fileRef}
          type="file"
          accept="application/json"
          className="hidden"
          onChange={(ev) => {
            const file = ev.target.files?.[0];
            ev.target.value = "";
            if (!file) return;
            void file.text().then((text) => useDrawStore.getState().importJson(JSON.parse(text))).catch(() => {});
          }}
        />
      </div>
      {treeOpen && <ObjectTree selectedId={selectedId} />}
      {selected && <Properties drawing={selected} timeframe={timeframe} treeOpen={treeOpen} />}
    </aside>
  );
}

function ToolButton({
  label,
  title,
  active,
  onClick,
  testId,
  id,
}: {
  id?: DrawMode;
  label: string;
  title: string;
  active: boolean;
  onClick: () => void;
  testId?: string;
}) {
  return (
    <button
      type="button"
      title={title}
      aria-pressed={active}
      data-testid={testId ?? (id ? `draw-tool-${id}` : undefined)}
      onClick={onClick}
      className={`rounded px-0 py-1 text-[11px] leading-none ${active ? "bg-sky-600 text-white" : "text-white/75 hover:bg-white/10"}`}
    >
      {label}
    </button>
  );
}

function ObjectTree({ selectedId }: { selectedId: string | null }) {
  const drawings = useDrawStore((s) => s.drawings);
  const rows = objectTreeRows(drawings);
  return (
    <div
      className="absolute left-11 top-2 z-30 max-h-[70vh] w-72 overflow-y-auto rounded border border-white/15 bg-[#0b0e14] p-2 text-[11px] text-white/80 shadow-lg"
      data-testid="object-tree"
    >
      <div className="mb-1 font-medium text-white">Object tree</div>
      {rows.length === 0 && <p className="text-white/40">No drawings on this symbol.</p>}
      <ul className="flex flex-col gap-1">
        {rows.map((row) => (
          <li
            key={row.id}
            data-testid={`tree-row-${row.id}`}
            className={`rounded px-1 py-1 ${row.id === selectedId ? "bg-white/10" : ""} ${row.hidden ? "opacity-50" : ""}`}
          >
            <button
              type="button"
              className="block w-full text-left"
              data-testid={`tree-select-${row.id}`}
              onClick={() => {
                useDrawStore.getState().setSelected(row.id);
                useDrawStore.getState().setTool("cursor");
              }}
            >
              <span className="text-white">{TOOL_LABEL[row.tool]}</span>
              <span className="ml-2 text-white/50">{row.drawnOn || "—"}</span>
              <span className="ml-2 text-white/50">{formatCrosshairTime(row.createdAt, "15m")}</span>
            </button>
            <div className="mt-1 flex gap-1">
              <TreeAction
                testId={`tree-hide-${row.id}`}
                title={row.hidden ? "Show" : "Hide"}
                label={row.hidden ? "show" : "hide"}
                onClick={() => useDrawStore.getState().patchDrawing(row.id, { hidden: !row.hidden })}
              />
              <TreeAction
                testId={`tree-lock-${row.id}`}
                title={row.locked ? "Unlock" : "Lock"}
                label={row.locked ? "unlock" : "lock"}
                onClick={() => useDrawStore.getState().patchDrawing(row.id, { locked: !row.locked })}
              />
              <TreeAction testId={`tree-zoom-${row.id}`} title="Zoom to" label="zoom" onClick={() => useDrawStore.getState().requestZoom(row.id)} />
              <TreeAction
                testId={`tree-delete-${row.id}`}
                title="Delete"
                label="delete"
                disabled={row.locked}
                onClick={() => useDrawStore.getState().remove(row.id)}
              />
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function TreeAction({
  testId,
  title,
  label,
  disabled,
  onClick,
}: {
  testId: string;
  title: string;
  label: string;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      title={title}
      data-testid={testId}
      disabled={disabled}
      onClick={onClick}
      className="rounded px-1 text-white/70 hover:bg-white/10 disabled:opacity-30"
    >
      {label}
    </button>
  );
}

function Properties({ drawing, timeframe, treeOpen }: { drawing: Drawing; timeframe: string; treeOpen: boolean }) {
  const update = (patch: Partial<Drawing>): void => useDrawStore.getState().changeSelected(patch);
  const style = drawing.style;
  const setStyle = (partial: Partial<Drawing["style"]>): void => update({ style: { ...style, ...partial } });
  const a = drawing.anchors[0];
  const b = drawing.anchors[1];
  return (
    <div className={`absolute top-2 z-30 w-56 rounded border border-white/15 bg-[#0b0e14] p-2 text-[11px] text-white/80 shadow-lg ${treeOpen ? "left-[21rem]" : "left-11"}`} data-testid="draw-properties">
      <div className="mb-1 font-medium text-white">{drawing.tool === "long_position" || drawing.tool === "short_position" ? positionToolLabel(drawing.tool) : drawing.tool}</div>
      <label className="mb-1 flex items-center justify-between gap-2">
        Colour
        <input type="color" value={toHex(style.color)} onChange={(ev) => setStyle({ color: ev.target.value })} />
      </label>
      <label className="mb-1 flex items-center justify-between gap-2">
        Width
        <input
          type="number"
          min={1}
          max={8}
          value={style.width}
          onChange={(ev) => setStyle({ width: Number(ev.target.value) || 1 })}
          className="w-16 rounded border border-white/15 bg-transparent px-1 py-0.5"
        />
      </label>
      <label className="mb-1 flex items-center justify-between gap-2">
        Line
        <select
          value={style.lineStyle}
          onChange={(ev) => setStyle({ lineStyle: ev.target.value as LineStyleName })}
          className="rounded border border-white/15 bg-[#0b0e14] px-1 py-0.5"
        >
          <option value="solid">Solid</option>
          <option value="dashed">Dashed</option>
          <option value="dotted">Dotted</option>
        </select>
      </label>
      <label className="mb-1 flex items-center gap-2">
        <input type="checkbox" checked={style.extendLeft} onChange={(ev) => setStyle({ extendLeft: ev.target.checked })} />
        Extend left
      </label>
      <label className="mb-1 flex items-center gap-2">
        <input type="checkbox" checked={style.extendRight} onChange={(ev) => setStyle({ extendRight: ev.target.checked })} />
        Extend right
      </label>
      {drawing.tool === "rectangle" && (
        <label className="mb-1 flex items-center justify-between gap-2">
          Fill
          <span className="flex items-center gap-1">
            <input type="color" value={toHex(style.fill ?? "#2962ff")} onChange={(ev) => setStyle({ fill: ev.target.value })} />
            <button type="button" className="text-white/50" onClick={() => setStyle({ fill: null })}>
              none
            </button>
          </span>
        </label>
      )}
      {drawing.tool === "text" && (
        <input
          value={drawing.text}
          onChange={(ev) => update({ text: ev.target.value })}
          className="mb-1 w-full rounded border border-white/15 bg-transparent px-1 py-0.5"
        />
      )}
      {drawing.tool === "measure" && a && b && <MeasureReadout a={a} b={b} timeframe={timeframe} />}
      {(drawing.tool === "long_position" || drawing.tool === "short_position") && <PositionFields drawing={drawing} />}
      <fieldset className="mt-2 border-t border-white/10 pt-1" data-testid="draw-timeframes">
        <legend className="mb-1 text-white/60">Show on</legend>
        <div className="grid grid-cols-4 gap-1">
          {TIMEFRAMES.map((tf) => {
            const checked = drawing.showOn == null || drawing.showOn.includes(tf);
            return (
              <label key={tf} className="flex items-center gap-1">
                <input
                  type="checkbox"
                  checked={checked}
                  data-testid={`draw-tf-${tf}`}
                  onChange={() => {
                    const current = drawing.showOn == null ? [...TIMEFRAMES] : [...drawing.showOn];
                    const next = current.includes(tf) ? current.filter((item) => item !== tf) : [...current, tf];
                    const all = TIMEFRAMES.every((item) => next.includes(item));
                    update({ showOn: all ? null : next });
                  }}
                />
                {tf}
              </label>
            );
          })}
        </div>
      </fieldset>
    </div>
  );
}

function PositionFields({ drawing }: { drawing: Drawing }) {
  const settings = drawing.position ?? defaultPositionSettings();
  const side = drawing.tool === "short_position" ? "short" : "long";
  const entry = drawing.anchors[0]?.price ?? 0;
  const views = usePositionViews();
  const view = views.find((item) => item.id === drawing.id);
  const write = (position: PositionSettings): void => useDrawStore.getState().changeSelected({ position });
  const setPrice = (role: "target" | "stop", points: number): void => {
    const index = role === "target" ? 1 : 2;
    const current = drawing.anchors[index];
    if (!current) return;
    const anchors = drawing.anchors.map((anchor, i) => (i === index ? { time: anchor.time, price: priceOf(side, entry, points, role) } : anchor));
    useDrawStore.getState().changeSelected({ anchors });
  };
  return (
    <div className="mt-1 border-t border-white/10 pt-1" data-testid="position-settings">
      <label className="mb-1 flex items-center justify-between gap-2">
        Account
        <input type="number" value={settings.accountSize} onChange={(ev) => write({ ...settings, accountSize: Number(ev.target.value) || 0 })} className="w-24 rounded border border-white/15 bg-transparent px-1 py-0.5" />
      </label>
      <label className="mb-1 flex items-center justify-between gap-2">
        Risk
        <select value={settings.riskMode} onChange={(ev) => write({ ...settings, riskMode: ev.target.value as PositionSettings["riskMode"] })} className="rounded border border-white/15 bg-[#0b0e14] px-1 py-0.5">
          <option value="percent">Percent</option>
          <option value="rupees">Rupees</option>
        </select>
      </label>
      {settings.riskMode === "percent" ? (
        <label className="mb-1 flex items-center justify-between gap-2">
          Risk %
          <input type="number" value={settings.riskPercent} onChange={(ev) => write({ ...settings, riskPercent: Number(ev.target.value) || 0 })} className="w-16 rounded border border-white/15 bg-transparent px-1 py-0.5" />
        </label>
      ) : (
        <label className="mb-1 flex items-center justify-between gap-2">
          Risk ₹
          <input type="number" value={settings.riskRupees} onChange={(ev) => write({ ...settings, riskRupees: Number(ev.target.value) || 0 })} className="w-24 rounded border border-white/15 bg-transparent px-1 py-0.5" />
        </label>
      )}
      <label className="mb-1 flex items-center justify-between gap-2">
        Lot
        <input
          type="number"
          placeholder="table"
          value={settings.lotSize ?? ""}
          onChange={(ev) => write({ ...settings, lotSize: ev.target.value === "" ? null : Math.max(1, Math.floor(Number(ev.target.value) || 1)) })}
          className="w-16 rounded border border-white/15 bg-transparent px-1 py-0.5"
        />
      </label>
      <label className="mb-1 flex items-center justify-between gap-2">
        Levels
        <select value={settings.priceMode} onChange={(ev) => write({ ...settings, priceMode: ev.target.value as PositionSettings["priceMode"] })} className="rounded border border-white/15 bg-[#0b0e14] px-1 py-0.5">
          <option value="price">Price</option>
          <option value="points">Points</option>
        </select>
      </label>
      {settings.priceMode === "points" && (
        <>
          <label className="mb-1 flex items-center justify-between gap-2">
            Target pts
            <input type="number" value={pointsOf(side, entry, drawing.anchors[1]?.price ?? entry, "target")} onChange={(ev) => setPrice("target", Number(ev.target.value) || 0)} className="w-20 rounded border border-white/15 bg-transparent px-1 py-0.5" />
          </label>
          <label className="mb-1 flex items-center justify-between gap-2">
            Stop pts
            <input type="number" value={pointsOf(side, entry, drawing.anchors[2]?.price ?? entry, "stop")} onChange={(ev) => setPrice("stop", Number(ev.target.value) || 0)} className="w-20 rounded border border-white/15 bg-transparent px-1 py-0.5" />
          </label>
        </>
      )}
      <label className="mb-1 flex items-center justify-between gap-2">
        Profit
        <input type="color" value={toHex(settings.profitColor)} onChange={(ev) => write({ ...settings, profitColor: ev.target.value })} />
      </label>
      <label className="mb-1 flex items-center justify-between gap-2">
        Stop
        <input type="color" value={toHex(settings.stopColor)} onChange={(ev) => write({ ...settings, stopColor: ev.target.value })} />
      </label>
      <label className="mb-1 flex items-center gap-2">
        <input type="checkbox" checked={settings.compact} onChange={(ev) => write({ ...settings, compact: ev.target.checked })} />
        Compact
      </label>
      <label className="mb-1 flex items-center gap-2">
        <input type="checkbox" checked={settings.options} onChange={(ev) => write({ ...settings, options: ev.target.checked })} />
        Options
      </label>
      <div className="mt-1 whitespace-pre-wrap break-words text-white/90" data-testid="position-readout" title={view?.labels.some((label) => label.tooltip) ? "estimated" : undefined}>
        {(view?.labels ?? []).map((label) => label.text).join("\n") || "pending"}
      </div>
    </div>
  );
}

function MeasureReadout({
  a,
  b,
  timeframe,
}: {
  a: Drawing["anchors"][number];
  b: Drawing["anchors"][number];
  timeframe: string;
}) {
  const m = measure(a, b, timeframe);
  return (
    <p>
      {m.price.toFixed(2)} ({m.percent.toFixed(2)}%) · {m.bars} bars
    </p>
  );
}

function exportDrawings(): void {
  const { symbol, drawings } = useDrawStore.getState();
  const blob = new Blob([JSON.stringify({ symbol, drawings }, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${symbol || "drawings"}.json`;
  link.click();
  URL.revokeObjectURL(url);
}

function toHex(color: string): string {
  return /^#[0-9a-fA-F]{6}$/.test(color) ? color : "#2962ff";
}
