import { useCallback, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";

import { readJson, writeJson } from "./storage";

/** Simple resizable-column table shell (widths in localStorage). */
export function ResizableTable({
  storageKey,
  columns,
  headerExtra,
  children,
}: {
  storageKey: string;
  columns: { key: string; label: string; width?: number; numeric?: boolean; onSort?: () => void; sorted?: "asc" | "desc" | null }[];
  headerExtra?: ReactNode;
  children: ReactNode;
}) {
  const defaults = Object.fromEntries(columns.map((c) => [c.key, c.width ?? 110]));
  const [widths, setWidths] = useState<Record<string, number>>(() => ({ ...defaults, ...readJson(storageKey, {}) }));

  const startResize = useCallback(
    (key: string, e: ReactPointerEvent) => {
      e.preventDefault();
      e.stopPropagation();
      const startX = e.clientX;
      const startW = widths[key] ?? 110;
      const onMove = (ev: PointerEvent) => {
        const next = Math.max(60, startW + (ev.clientX - startX));
        setWidths((w) => {
          const out = { ...w, [key]: next };
          writeJson(storageKey, out);
          return out;
        });
      };
      const onUp = () => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [storageKey, widths],
  );

  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <table className="w-max min-w-full text-left text-[11px] tabular-nums">
        <thead className="sticky top-0 z-10 bg-[#0b0e14] text-white/50">
          <tr>
            {columns.map((c) => (
              <th
                key={c.key}
                className={`relative whitespace-nowrap py-1 pr-3 font-normal ${c.numeric ? "text-right" : ""}`}
                style={{ width: widths[c.key], minWidth: widths[c.key] }}
              >
                {c.onSort ? (
                  <button type="button" className="hover:text-white" onClick={c.onSort}>
                    {c.label}
                    {c.sorted === "asc" ? " ▲" : c.sorted === "desc" ? " ▼" : ""}
                  </button>
                ) : (
                  c.label
                )}
                <span
                  role="separator"
                  aria-orientation="vertical"
                  className="absolute inset-y-0 right-0 w-1 cursor-col-resize hover:bg-sky-500/40"
                  onPointerDown={(e) => startResize(c.key, e)}
                />
              </th>
            ))}
            {headerExtra}
          </tr>
        </thead>
        {children}
      </table>
    </div>
  );
}
