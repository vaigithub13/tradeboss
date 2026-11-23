/** One poller per name. A second tab waits on the Web Locks leader instead of firing its own request. */

export interface LockLike {
  request(name: string, options: { signal?: AbortSignal }, callback: (lock: unknown) => Promise<void>): Promise<void>;
}

export function holdLeader(name: string, start: () => () => void, locks?: LockLike | null): () => void {
  const api =
    locks === undefined
      ? typeof navigator !== "undefined"
        ? (navigator.locks as LockLike | undefined)
        : undefined
      : locks;
  if (!api?.request) {
    const stop = start();
    return () => stop();
  }
  let stop = (): void => {};
  let stopped = false;
  const controller = new AbortController();
  void api
    .request(name, { signal: controller.signal }, async () => {
      if (stopped) return;
      stop = start();
      await new Promise<void>((resolve) => {
        if (controller.signal.aborted) {
          resolve();
          return;
        }
        controller.signal.addEventListener("abort", () => resolve(), { once: true });
      });
      stop();
      stop = () => {};
    })
    .catch(() => {
      if (!stopped) stop = start();
    });
  return () => {
    stopped = true;
    stop();
    controller.abort();
  };
}

const inflight = new Map<string, Promise<unknown>>();

/** Overlapping callers of the same key share one request. */
export function coalesce<T>(key: string, run: () => Promise<T>): Promise<T> {
  const existing = inflight.get(key);
  if (existing) return existing as Promise<T>;
  const pending = run().finally(() => {
    if (inflight.get(key) === pending) inflight.delete(key);
  });
  inflight.set(key, pending);
  return pending;
}
