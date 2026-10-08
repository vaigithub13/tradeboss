import { useCallback, useRef, useState } from "react";

import { dragSize, readSize, writeSize } from "./storedSize";

/** A size the user changes by dragging an edge, remembered under `key`. `axis` x = width, y = height; `sign` -1
 * when the edge is on the left (or top) side, so dragging toward it grows the panel. */
export function useDragResize(key: string, fallback: number, min: number, max: () => number, axis: "x" | "y", sign: 1 | -1) {
  const [size, setSize] = useState(() => readSize(key, fallback, min, max()));
  const sizeRef = useRef(size);
  sizeRef.current = size;
  const onPointerDown = useCallback((e: React.PointerEvent) => {
    e.preventDefault();
    const start = sizeRef.current;
    const origin = axis === "x" ? e.clientX : e.clientY;
    const move = (ev: PointerEvent) => {
      const next = dragSize(start, (axis === "x" ? ev.clientX : ev.clientY) - origin, sign, min, max());
      sizeRef.current = next;
      setSize(next);
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      writeSize(key, sizeRef.current);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }, [key, min, max, axis, sign]);
  return { size, onPointerDown };
}
