/**
 * WebSocket client for /api/live/ws (one per tab; the backend shares ONE Upstox connection).
 *
 * - reconnects with backoff (1 s, 2 s, 4 s ... 10 s) and re-sends the current view
 * - a silent socket (no message for `watchdogMs`; the server sends a status every second) is
 *   closed and re-opened
 * - UI updates are throttled: bar messages are folded per symbol/timeframe and delivered at most
 *   once per `throttleMs` (~5 per second), always the NEWEST candles
 *
 * Time and sockets are injected so tests run without real timers or a network.
 */
import type { BarMessage, LiveStatus, LiveView, ServerMessage } from "./protocol";

export interface SocketLike {
  readyState: number;
  send: (data: string) => void;
  close: () => void;
  onopen: (() => void) | null;
  onmessage: ((ev: { data: unknown }) => void) | null;
  onclose: (() => void) | null;
  onerror: (() => void) | null;
}

export interface Timers {
  setTimeout: (fn: () => void, ms: number) => unknown;
  clearTimeout: (id: unknown) => void;
  now: () => number;
}

export type LinkState = "connecting" | "open" | "closed";

export interface LiveHandlers {
  onLink: (link: LinkState) => void;
  onStatus: (status: LiveStatus, receivedAt: number) => void;
  onBar: (msg: BarMessage) => void;
  onReload: (symbol: string) => void;
  onError?: (message: string) => void;
}

export interface LiveClientOptions {
  url: string;
  makeSocket: (url: string) => SocketLike;
  timers: Timers;
  throttleMs?: number;
  watchdogMs?: number;
  backoffMs?: readonly number[];
}

const OPEN = 1;
const DEFAULT_BACKOFF = [1000, 2000, 4000, 8000, 10000] as const;

export class LiveClient {
  private socket: SocketLike | null = null;
  private view: LiveView | null = null;
  private stopped = true;
  private attempt = 0;
  private retryTimer: unknown = null;
  private watchdogTimer: unknown = null;
  private flushTimer: unknown = null;
  private lastFlush = -Infinity;
  private pending = new Map<string, BarMessage>();

  private readonly throttleMs: number;
  private readonly watchdogMs: number;
  private readonly backoff: readonly number[];

  constructor(
    private readonly opts: LiveClientOptions,
    private readonly handlers: LiveHandlers,
  ) {
    this.throttleMs = opts.throttleMs ?? 200;
    this.watchdogMs = opts.watchdogMs ?? 6000;
    this.backoff = opts.backoffMs ?? DEFAULT_BACKOFF;
  }

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    this.attempt = 0;
    this.open();
  }

  stop(): void {
    this.stopped = true;
    const t = this.opts.timers;
    for (const id of [this.retryTimer, this.watchdogTimer, this.flushTimer]) if (id !== null) t.clearTimeout(id);
    this.retryTimer = this.watchdogTimer = this.flushTimer = null;
    this.pending.clear();
    const s = this.socket;
    this.socket = null;
    if (s) {
      s.onopen = s.onmessage = s.onclose = s.onerror = null;
      s.close();
    }
    this.handlers.onLink("closed");
  }

  /** Tell the server what the tab shows; re-sent automatically after every reconnect. */
  setView(view: LiveView | null): void {
    this.view = view;
    this.sendView();
  }

  private sendView(): void {
    const s = this.socket;
    if (!s || s.readyState !== OPEN || !this.view) return;
    s.send(JSON.stringify({ type: "view", ...this.view }));
  }

  private open(): void {
    if (this.stopped) return;
    this.handlers.onLink("connecting");
    const s = this.opts.makeSocket(this.opts.url);
    this.socket = s;
    s.onopen = () => {
      if (this.socket !== s) return;
      this.attempt = 0;
      this.handlers.onLink("open");
      this.sendView();
      this.armWatchdog();
    };
    s.onmessage = (ev) => {
      if (this.socket !== s) return;
      this.armWatchdog();
      this.handle(ev.data);
    };
    const lost = (): void => {
      if (this.socket !== s) return;
      this.socket = null;
      if (this.watchdogTimer !== null) this.opts.timers.clearTimeout(this.watchdogTimer);
      this.watchdogTimer = null;
      this.handlers.onLink("closed");
      this.scheduleReconnect();
    };
    s.onclose = lost;
    s.onerror = () => {
      // an error is always followed by close in browsers; make sure we notice in any case
      try {
        s.close();
      } catch {
        /* ignore */
      }
      lost();
    };
  }

  private scheduleReconnect(): void {
    if (this.stopped || this.retryTimer !== null) return;
    const wait = this.backoff[Math.min(this.attempt, this.backoff.length - 1)] ?? 10000;
    this.attempt++;
    this.retryTimer = this.opts.timers.setTimeout(() => {
      this.retryTimer = null;
      this.open();
    }, wait);
  }

  private armWatchdog(): void {
    const t = this.opts.timers;
    if (this.watchdogTimer !== null) t.clearTimeout(this.watchdogTimer);
    this.watchdogTimer = t.setTimeout(() => {
      this.watchdogTimer = null;
      const s = this.socket;
      if (!s) return;
      this.socket = null;
      s.onopen = s.onmessage = s.onclose = s.onerror = null;
      try {
        s.close();
      } catch {
        /* ignore */
      }
      this.handlers.onLink("closed");
      this.scheduleReconnect();
    }, this.watchdogMs);
  }

  private handle(raw: unknown): void {
    let msg: ServerMessage;
    try {
      msg = JSON.parse(String(raw)) as ServerMessage;
    } catch {
      return;
    }
    switch (msg.type) {
      case "status": {
        const { type: _type, ...status } = msg;
        this.handlers.onStatus(status, this.opts.timers.now());
        break;
      }
      case "bar":
        this.pending.set(`${msg.symbol}|${msg.timeframe}`, msg); // newest wins
        this.scheduleFlush();
        break;
      case "reload":
        this.handlers.onReload(msg.symbol);
        break;
      case "error":
        this.handlers.onError?.(msg.message);
        break;
      default:
        break;
    }
  }

  private scheduleFlush(): void {
    if (this.flushTimer !== null) return;
    const t = this.opts.timers;
    const wait = Math.max(0, this.lastFlush + this.throttleMs - t.now());
    this.flushTimer = t.setTimeout(() => {
      this.flushTimer = null;
      this.flush();
    }, wait);
  }

  private flush(): void {
    this.lastFlush = this.opts.timers.now();
    const batch = [...this.pending.values()];
    this.pending.clear();
    for (const m of batch) this.handlers.onBar(m);
  }
}
