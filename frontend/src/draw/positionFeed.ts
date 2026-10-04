import { useSyncExternalStore } from "react";

import { fetchCandles, getJson, postJson, type Candle } from "../api/client";
import type { Drawing } from "./model";
import {
  defaultPositionSettings,
  isPositionTool,
  positionLabels,
  positionLevels,
  positionOutcome,
  positionSize,
  type Bar,
  type PositionLabel,
  type PositionOutcome,
} from "./position";

const IST = 19800;

export interface PositionView {
  id: string;
  labels: PositionLabel[];
  outcome: PositionOutcome;
  entryTime: number;
  rightTime: number;
  entry: number;
  target: number;
  stop: number;
  profitColor: string;
  stopColor: string;
  side: "long" | "short";
  optionLabel: string | null;
}

let published: PositionView[] = [];
const listeners = new Set<() => void>();

export function publishPositionViews(views: readonly PositionView[]): void {
  published = [...views];
  for (const listener of listeners) listener();
}

export function usePositionViews(): readonly PositionView[] {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => published,
  );
}

export function istDate(time: number): string {
  return new Date((time + IST) * 1000).toISOString().slice(0, 10);
}

export function asBar(candle: Candle): Bar {
  return { time: candle.time, open: candle.open, high: candle.high, low: candle.low, close: candle.close };
}

export function positionViews(
  drawings: readonly Drawing[],
  candles: readonly Candle[],
  minutes: readonly Bar[],
  lots: Readonly<Record<string, number>>,
  notes: Readonly<Record<string, string>>,
  cursor: number | null,
): PositionView[] {
  const bars = candles.map(asBar);
  return drawings.filter((drawing) => isPositionTool(drawing.tool)).flatMap((drawing) => {
    const entry = drawing.anchors[0];
    const right = drawing.anchors[1];
    const stop = drawing.anchors[2];
    if (!entry || !right || !stop) return [];
    const side = drawing.tool === "long_position" ? "long" : "short";
    const settings = drawing.position ?? defaultPositionSettings();
    const levels = positionLevels(side, drawing.anchors);
    const size = positionSize(levels, settings, lots[drawing.id] ?? 0);
    const outcome = positionOutcome(side, drawing.anchors, bars, minutes, cursor, size.quantity);
    const optionLabel = settings.options ? notes[drawing.id] ?? null : null;
    const labels = positionLabels(levels, size, outcome, settings.compact, optionLabel);
    return [{
      id: drawing.id,
      labels,
      outcome,
      entryTime: entry.time,
      rightTime: right.time,
      entry: entry.price,
      target: right.price,
      stop: stop.price,
      profitColor: settings.profitColor,
      stopColor: settings.stopColor,
      side,
      optionLabel,
    }];
  });
}

export async function loadPositionMinutes(
  symbol: string,
  drawings: readonly Drawing[],
  cursor: number | null,
  signal: AbortSignal,
): Promise<Bar[]> {
  const positions = drawings.filter((drawing) => isPositionTool(drawing.tool));
  const times = positions.flatMap((drawing) => drawing.anchors.map((anchor) => anchor.time));
  if (times.length === 0) return [];
  const from = Math.min(...times);
  const to = cursor == null ? Math.max(...times) : Math.min(cursor, Math.max(...times));
  if (to < from) return [];
  const res = await fetchCandles({ symbol, timeframe: "1m", from, to, cursor: cursor ?? undefined }, signal);
  return res.candles.map(asBar);
}

export async function loadPositionLots(
  symbol: string,
  drawings: readonly Drawing[],
  signal: AbortSignal,
): Promise<Record<string, number>> {
  const lots: Record<string, number> = {};
  const cache = new Map<string, number>();
  for (const drawing of drawings) {
    if (!isPositionTool(drawing.tool) || drawing.position?.lotSize) continue;
    const entry = drawing.anchors[0];
    if (!entry) continue;
    const day = istDate(entry.time);
    let lot = cache.get(day);
    if (lot == null) {
      const res = await getJson<{ lot: number | null }>(`/api/position-lot?symbol=${encodeURIComponent(symbol)}&on=${day}`, signal);
      lot = res.lot ?? 0;
      cache.set(day, lot);
    }
    lots[drawing.id] = lot;
  }
  return lots;
}

export async function loadOptionNotes(
  symbol: string,
  views: readonly PositionView[],
  drawings: readonly Drawing[],
  cursor: number | null,
  signal: AbortSignal,
): Promise<Record<string, string>> {
  if (symbol !== "NIFTY50" && symbol !== "NIFTY") return {};
  const notes: Record<string, string> = {};
  for (const view of views) {
    const drawing = drawings.find((item) => item.id === view.id);
    const settings = drawing?.position;
    if (!settings?.options) continue;
    if (view.outcome.status === "pending" || view.outcome.status === "not_entered") continue;
    const targetTime = view.outcome.targetTime ?? view.rightTime;
    const stopTime = view.outcome.stopTime ?? view.rightTime;
    const res = await postJson<{ label: string | null }>(
      "/api/position-option",
      {
        symbol,
        side: view.side,
        entry: view.entry,
        target: view.target,
        stop: view.stop,
        entry_time: view.entryTime,
        target_time: targetTime,
        stop_time: stopTime,
        status: view.outcome.status,
        exit_index: view.outcome.exitPrice,
        exit_time: view.outcome.endTime,
        as_of: cursor,
        account_size: settings.accountSize,
        risk_mode: settings.riskMode,
        risk_percent: settings.riskPercent,
        risk_rupees: settings.riskRupees,
      },
      signal,
    );
    if (res.label) notes[view.id] = res.label;
  }
  return notes;
}
