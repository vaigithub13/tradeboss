/** Apply a live tick to the forming candle, then hand the closed candles to the study. */

export function pushLiveBar<T>(
  current: readonly T[],
  incoming: readonly T[],
  update: (bar: T) => void,
  key: (bar: T) => number,
): T[] {
  for (const bar of incoming) update(bar);
  const byTime = new Map(current.map((bar) => [key(bar), bar]));
  for (const bar of incoming) byTime.set(key(bar), bar);
  return [...byTime.values()].sort((a, b) => key(a) - key(b));
}
