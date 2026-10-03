export interface HealthResponse {
  status: "ok";
  service: string;
  version: string;
  time_ist: string;
  live_trading: boolean;
}

export async function fetchHealth(signal?: AbortSignal): Promise<HealthResponse> {
  const res = await fetch("/api/health", signal ? { signal } : undefined);
  if (!res.ok) {
    throw new Error(`Health check failed: HTTP ${res.status}`);
  }
  return (await res.json()) as HealthResponse;
}

// ---------------------------------------------------------------- candles

export const TIMEFRAMES = ["1m", "3m", "5m", "15m", "30m", "1h", "1D", "1W"] as const;
export type Timeframe = (typeof TIMEFRAMES)[number];

export const SESSION_TYPES = ["normal", "weekend_full", "special_short", "muhurat"] as const;
export type SessionType = (typeof SESSION_TYPES)[number];

/** Candle contract (PROJECT_PLAN.md section 4). `time` = unix seconds of the bar start. */
export interface Candle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  oi: number | null;
}

export interface CandlesResponse {
  symbol: string;
  timeframe: Timeframe;
  source_minutes: number;
  sessions: SessionType[];
  /** with `limit`: are there candles older than the first one returned? */
  has_more: boolean;
  /** with `after`: are there candles newer than the last one returned? */
  has_more_newer: boolean;
  candles: Candle[];
}

export interface SymbolInfo {
  /** id used in the candle / indicator APIs (a folder name, e.g. NIFTY50) */
  symbol: string;
  /** what the legend shows (e.g. NIFTY, RELIANCE); falls back to `symbol` */
  display_name: string;
  instrument_key: string | null;
  kind: InstrumentKind | null;
  base_timeframe: string;
  available_timeframes: Timeframe[];
  first_time: number;
  last_time: number;
}

export interface SymbolsResponse {
  default_sessions: SessionType[];
  session_types: SessionType[];
  timeframes: Timeframe[];
  symbols: SymbolInfo[];
}

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export interface CandlesQuery {
  symbol: string;
  timeframe: Timeframe;
  /** unix seconds, inclusive */
  from?: number;
  /** unix seconds, inclusive */
  to?: number;
  /** session types to include; omit to use the server default */
  sessions?: readonly SessionType[];
  /** newest N candles (lazy loading) */
  limit?: number;
  /** only candles that START before this unix time (exclusive); pairs with `limit` */
  before?: number;
  /** the OLDEST `limit` candles that start after this unix time (exclusive); needs `limit` */
  after?: number;
}

export function candlesUrl(q: CandlesQuery): string {
  const p = new URLSearchParams({ symbol: q.symbol, timeframe: q.timeframe });
  if (q.from !== undefined) p.set("from", String(q.from));
  if (q.to !== undefined) p.set("to", String(q.to));
  if (q.sessions !== undefined) p.set("sessions", q.sessions.join(","));
  if (q.limit !== undefined) p.set("limit", String(q.limit));
  if (q.before !== undefined) p.set("before", String(q.before));
  if (q.after !== undefined) p.set("after", String(q.after));
  return `/api/candles?${p.toString()}`;
}

async function parseResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // keep generic message
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

export async function getJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  return parseResponse<T>(await fetch(url, signal ? { signal } : undefined));
}

export async function postJson<T>(url: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    ...(signal ? { signal } : {}),
  });
  return parseResponse<T>(res);
}

export function fetchCandles(q: CandlesQuery, signal?: AbortSignal): Promise<CandlesResponse> {
  return getJson<CandlesResponse>(candlesUrl(q), signal);
}

export function fetchSymbols(signal?: AbortSignal): Promise<SymbolsResponse> {
  return getJson<SymbolsResponse>("/api/symbols", signal);
}

// ---------------------------------------------------------------- upstox (read-only data)

export type InstrumentKind = "index" | "equity" | "future" | "option";
export const INSTRUMENT_KINDS: readonly InstrumentKind[] = ["index", "equity", "future", "option"];

export type TokenState = "valid" | "invalid" | "expired" | "missing" | "unreachable";

export interface TokenStatus {
  state: TokenState;
  message: string;
  expires_at: string | null;
  days_left: number | null;
  expires_soon: boolean;
  market_status: string | null;
  checked_at: string | null;
}

export interface InstrumentHit {
  instrument_key: string;
  symbol: string;
  name: string;
  kind: InstrumentKind;
  segment: string;
  instrument_type: string;
  expiry: string | null;
  strike: number | null;
  lot_size: number | null;
  underlying_key: string | null;
  /** the symbol id the chart uses for this instrument */
  symbol_id: string;
  has_data: boolean;
  has_1m: boolean;
}

export interface InstrumentSearchResponse {
  snapshot_date: string | null;
  message: string | null;
  items: InstrumentHit[];
}

export interface SyncJob {
  id: string;
  instrument_key: string;
  symbol: string;
  status: "running" | "done" | "error";
  windows_total: number;
  windows_done: number;
  bars_added: number;
  message: string;
  error: string | null;
  auth_error: boolean;
  started_at: string;
  finished_at: string | null;
}

export interface Coverage {
  instrument_key: string;
  symbol_id: string;
  /** [from, to] IST dates (YYYY-MM-DD) of 1m data already fetched */
  covered: [string, string][];
  min_history_date: string;
}

export function fetchTokenStatus(refresh = false, signal?: AbortSignal): Promise<TokenStatus> {
  return getJson<TokenStatus>(`/api/upstox/status${refresh ? "?refresh=true" : ""}`, signal);
}

export function searchInstruments(
  q: string,
  kind: InstrumentKind | null,
  limit = 40,
  signal?: AbortSignal,
): Promise<InstrumentSearchResponse> {
  const p = new URLSearchParams({ q, limit: String(limit) });
  if (kind) p.set("kind", kind);
  return getJson<InstrumentSearchResponse>(`/api/instruments/search?${p.toString()}`, signal);
}

export function startSync(instrumentKey: string, fromDate?: string): Promise<SyncJob> {
  return postJson<SyncJob>("/api/history/sync", {
    instrument_key: instrumentKey,
    ...(fromDate ? { from_date: fromDate } : {}),
  });
}

export function fetchSyncJob(id: string, signal?: AbortSignal): Promise<SyncJob> {
  return getJson<SyncJob>(`/api/history/jobs/${encodeURIComponent(id)}`, signal);
}

export function fetchCoverage(instrumentKey: string, signal?: AbortSignal): Promise<Coverage> {
  return getJson<Coverage>(`/api/history/coverage?instrument_key=${encodeURIComponent(instrumentKey)}`, signal);
}
