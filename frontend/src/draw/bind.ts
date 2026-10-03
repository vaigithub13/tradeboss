import type { IChartApi, ISeriesApi, MouseEventParams, Time } from "lightweight-charts";

import type { Candle, Timeframe } from "../api/client";
import { barStart, setAnchor, snapPrice, whitespaceTimes, type Anchor, type Drawing } from "./model";
import type { DrawPrimitive } from "./primitive";
import { useDrawStore } from "./store";

export interface DrawBag {
  candles: readonly Candle[];
  timeframe: Timeframe;
  cursor: number | null;
}

/** Click-to-place, drag a selected drawing, and the eraser. Reads the drawing store at event time. */
export function bindDrawings(
  chart: IChartApi,
  series: ISeriesApi<"Candlestick">,
  el: HTMLElement,
  primitive: DrawPrimitive,
  bag: { current: DrawBag },
): () => void {
  const local = (ev: PointerEvent): { x: number; y: number } => {
    const rect = el.getBoundingClientRect();
    return { x: ev.clientX - rect.left, y: ev.clientY - rect.top };
  };

  const resolve = (x: number, y: number): Anchor | null => {
    const price = series.coordinateToPrice(y);
    if (price == null || Number.isNaN(price)) return null;
    const { candles, timeframe, cursor: _cursor } = bag.current;
    const holidays = useDrawStore.getState().holidays;
    const last = candles[candles.length - 1];
    const logical = chart.timeScale().coordinateToLogical(x);
    let time: number | null = null;
    if (logical != null && last && logical > candles.length - 1.5) {
      const steps = Math.max(1, Math.min(80, Math.round(logical - (candles.length - 1))));
      const slots = whitespaceTimes(last.time, timeframe, steps, holidays);
      time = slots[steps - 1] ?? null;
    }
    if (time == null) {
      const raw = chart.timeScale().coordinateToTime(x);
      if (typeof raw === "number") time = raw;
    }
    if (time == null) return null;
    const magnet = useDrawStore.getState().magnet;
    const mapped = barStart(time, timeframe);
    const bar = candles.find((candle) => candle.time === mapped);
    return { time, price: magnet && bar ? snapPrice(price, bar) : price };
  };

  const knownAt = (): number => {
    const { cursor, candles } = bag.current;
    return cursor ?? candles[candles.length - 1]?.time ?? Math.floor(Date.now() / 1000);
  };

  const onClick = (param: MouseEventParams<Time>): void => {
    const point = param.point;
    if (!point) return;
    const store = useDrawStore.getState();
    if (store.tool === "cursor") return;
    if (store.tool === "eraser") {
      const hit = primitive.pick(point.x, point.y);
      if (hit && hit.id !== "draft") store.remove(hit.id);
      return;
    }
    const anchor = resolve(point.x, point.y);
    if (anchor) store.place(anchor, knownAt());
  };

  const onMove = (param: MouseEventParams<Time>): void => {
    const store = useDrawStore.getState();
    if (!store.draft || !param.point) {
      if (store.hover) store.setHover(null);
      return;
    }
    store.setHover(resolve(param.point.x, param.point.y));
  };

  let gesture: { id: string; handle: number | null; origin: Drawing; x: number; y: number; pushed: boolean } | null = null;

  const onPointerDown = (ev: PointerEvent): void => {
    if (ev.button !== 0) return;
    const store = useDrawStore.getState();
    if (store.tool !== "cursor") return;
    const { x, y } = local(ev);
    const hit = primitive.pick(x, y);
    if (!hit || hit.id === "draft") {
      if (store.selectedId) store.setSelected(null);
      return;
    }
    ev.stopPropagation();
    ev.preventDefault();
    const origin = store.drawings.find((item) => item.id === hit.id);
    if (!origin) return;
    store.setSelected(hit.id);
    gesture = { id: hit.id, handle: hit.handle, origin, x, y, pushed: false };
  };

  const onPointerMove = (ev: PointerEvent): void => {
    if (!gesture) return;
    const { x, y } = local(ev);
    if (Math.hypot(x - gesture.x, y - gesture.y) < 3) return;
    const store = useDrawStore.getState();
    if (store.lockAll) return;
    const start = resolve(gesture.x, gesture.y);
    const next = resolve(x, y);
    if (!start || !next) return;
    if (!gesture.pushed) {
      store.remember();
      gesture.pushed = true;
    }
    const moved =
      gesture.handle == null
        ? {
            ...gesture.origin,
            anchors: gesture.origin.anchors.map((anchor) => ({
              time: anchor.time + (next.time - start.time),
              price: anchor.price + (next.price - start.price),
            })),
          }
        : setAnchor(gesture.origin, gesture.handle, next);
    const drawings = useDrawStore.getState().drawings.map((item) => (item.id === gesture?.id ? moved : item));
    store.preview(drawings);
  };

  const onPointerUp = (): void => {
    gesture = null;
  };

  chart.subscribeClick(onClick);
  chart.subscribeCrosshairMove(onMove);
  el.addEventListener("pointerdown", onPointerDown, true);
  window.addEventListener("pointermove", onPointerMove);
  window.addEventListener("pointerup", onPointerUp);

  return () => {
    chart.unsubscribeClick(onClick);
    chart.unsubscribeCrosshairMove(onMove);
    el.removeEventListener("pointerdown", onPointerDown, true);
    window.removeEventListener("pointermove", onPointerMove);
    window.removeEventListener("pointerup", onPointerUp);
  };
}
