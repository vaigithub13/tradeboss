import { useEffect, useRef, useState, type ReactNode } from "react";

interface Props {
  label: ReactNode;
  title?: string;
  /** content, or a function that receives `close` (to close the panel after a choice) */
  children: ReactNode | ((close: () => void) => ReactNode);
  /** width class for the panel */
  panelClassName?: string;
}

/** Button that toggles a dropdown panel; closes on outside click / Escape. */
export function Popover({ label, title, children, panelClassName = "w-64" }: Props) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        title={title}
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className={`rounded px-2.5 py-1 text-xs font-medium transition-colors ${
          open ? "bg-white/15 text-white" : "text-white/70 hover:bg-white/10"
        }`}
      >
        {label}
      </button>
      {open && (
        <div
          className={`absolute left-0 top-full z-30 mt-1 rounded-md border border-white/10 bg-[#141821] p-2 shadow-xl ${panelClassName}`}
        >
          {typeof children === "function" ? children(() => setOpen(false)) : children}
        </div>
      )}
    </div>
  );
}
