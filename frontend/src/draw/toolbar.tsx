import { useEffect, useRef } from "react";

import { getJson } from "../api/client";
import { useChartStore } from "../store/chartStore";
import { measure, type Drawing, type LineStyleName } from "./model";
import { useDrawStore, type DrawMode } from "./store";

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
    void getJson<{ holidays?: string[] }>("/api/ai/settings").then((settings) => {
      if (!cancel && settings.holidays) useDrawStore.getState().setHolidays(settings.holidays);
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
      {selected && <Properties drawing={selected} timeframe={timeframe} />}
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

function Properties({ drawing, timeframe }: { drawing: Drawing; timeframe: string }) {
  const update = (patch: Partial<Drawing>): void => useDrawStore.getState().changeSelected(patch);
  const style = drawing.style;
  const setStyle = (partial: Partial<Drawing["style"]>): void => update({ style: { ...style, ...partial } });
  const a = drawing.anchors[0];
  const b = drawing.anchors[1];
  return (
    <div className="absolute left-11 top-2 z-30 w-56 rounded border border-white/15 bg-[#0b0e14] p-2 text-[11px] text-white/80 shadow-lg" data-testid="draw-properties">
      <div className="mb-1 font-medium text-white">{drawing.tool}</div>
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
