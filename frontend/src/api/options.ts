import { ApiError, getJson } from "./client";

export type OptionsStatus = "LIVE" | "MARKET_CLOSED";
export type BuildupLabel =
  | "Long Build-up"
  | "Short Build-up"
  | "Short Covering"
  | "Long Unwinding"
  | "Neutral";
export type NiftyBias = "Bullish" | "Bearish";
export type PcrRead = "Bullish" | "Neutral" | "Bearish" | "Strong Bullish" | "Strong Bearish";
export type StrikeDirection = "Bullish" | "Bearish";
export type HoldMode = "intraday" | "1d" | "2d";

export interface OptionsLeg {
  oi: number;
  prev_oi: number;
  oi_chg: number;
  oi_chg_pct: number | null;
  volume: number;
  iv: number | null;
  ltp: number;
  close: number;
  price_chg: number;
  bid: number;
  ask: number;
  spread_pct: number | null;
  delta?: number | null;
  gamma?: number | null;
  theta?: number | null;
  vega?: number | null;
  buildup?: BuildupLabel;
  buildup_nifty?: NiftyBias | null;
}

export type OptionsStrength = "Strong" | "Medium" | "Weak";

export interface OptionsLevel {
  strike: number;
  oi: number;
  tag: string;
  strength?: OptionsStrength;
  oi_pct?: number;
  edge?: boolean;
  p_close?: number | null;
  p_touch?: number | null;
}

export interface OptionsWall {
  strike: number;
  oi: number;
}

export interface OptionsMajorWalls {
  call: OptionsWall | null;
  put: OptionsWall | null;
}

export interface OptionsPivot {
  strike: number;
  call_oi: number;
  put_oi: number;
  heavy_call: boolean;
  heavy_put: boolean;
  tag: "PIVOT";
  read: string;
  p_close?: number | null;
  p_touch?: number | null;
}

export interface OptionsMaxPain {
  strike: number;
  pain?: number;
  distance?: number;
  tag: string;
  p_close?: number | null;
  p_touch?: number | null;
}

export interface PcrValue {
  value: number | null;
  display: number | string;
  reason?: string | null;
  read?: PcrRead | null;
  one_sided?: boolean;
  capped_label?: string | null;
  /** Put ΔOI − Call ΔOI (raw units); shown for PCR ΔOI. */
  net_doi?: number | null;
  /** Near-ATM / full: single-strike OI concentration note. */
  concentration?: string | null;
}

export interface OptionsPcrs {
  full: PcrValue;
  near_atm: PcrValue;
  change_oi: PcrValue;
}

export interface BuildupAnalysis {
  zone_strikes: number[];
  call_doi: number;
  put_doi: number;
  top_call_additions: { strike: number; oi_chg: number }[];
  top_put_additions: { strike: number; oi_chg: number }[];
  summary: string;
  bias: "Bullish" | "Bearish" | "Mixed";
}

export interface OptionsRow {
  strike: number;
  call: OptionsLeg;
  put: OptionsLeg;
  atm: boolean;
  tag?: string | null;
  strength?: OptionsStrength | null;
  edge?: boolean;
  max_pain?: boolean;
  call_itm: boolean;
  put_itm: boolean;
}

export interface IvPercentile {
  available: boolean;
  label: string;
  percentile: number | null;
  days: number;
  need: number;
}

export interface ExpectedMoveBand {
  move: number;
  upper: number;
  lower: number;
}

export interface IvPanel {
  atm_iv: number | null;
  call_iv?: number | null;
  put_iv?: number | null;
  india_vix: number | null;
  diff: number | null;
  verdict: "Options expensive" | "Options cheap" | "Fair" | null;
  skew: number | null;
  percentile: IvPercentile;
  iv_unit?: string;
  theta_unit?: string;
  /** Trading-day T convention used for the IV 1σ info line. */
  t_convention?: string;
  one_sigma_iv?: (ExpectedMoveBand & { label?: string; available?: boolean; info_only?: boolean }) | null;
}

export interface ExpectedMove {
  t_years: number;
  trading_days?: number;
  t_convention?: string;
  implied_vs?: number | null;
  range_move?: number;
  market_priced?: ExpectedMoveBand & { label?: string };
  straddle_range?: ExpectedMoveBand & { label?: string };
  one_sigma?: ExpectedMoveBand & { label?: string; available?: boolean; info_only?: boolean };
  straddle: ExpectedMoveBand;
  iv: ExpectedMoveBand | null;
  primary: string;
  move: number;
  upper: number;
  lower: number;
}

export type TargetMode = "Auto" | "R1" | "R2" | "R3" | "S1" | "S2" | "S3";

export type StrikeCheckStatus = "pass" | "warn" | "fail";

export interface StrikeCheck {
  id: string;
  label: string;
  status: StrikeCheckStatus;
  detail?: string | null;
}

export interface StrikeCandidate {
  bucket: "ITM" | "ATM" | "OTM" | string;
  strike: number;
  side: "call" | "put";
  ltp: number;
  delta: number;
  gamma: number;
  theta: number;
  theta_pct_premium: number | null;
  iv: number | null;
  spread_pct: number | null;
  lot_size: number | null;
  cost_per_lot: number | null;
  est_at_target: number | null;
  est_at_stop: number | null;
  est_gain?: number | null;
  est_loss?: number | null;
  reward_risk?: number | null;
  win_chance?: number | null;
  win_chance_driftless?: number | null;
  breakeven_win?: number | null;
  ev_per_lot?: number | null;
  round_trip_charges?: number | null;
  checks?: StrikeCheck[];
  passed?: boolean;
  failed_checks?: string[];
  highlighted?: boolean;
  closest_failed?: boolean;
}

export interface StrikePickerSide {
  direction: StrikeDirection;
  side: "call" | "put";
  target: number | null;
  target_distance?: number | null;
  target_mode?: TargetMode | string | null;
  target_tag?: string | null;
  auto_resolved_tag?: string | null;
  auto_message?: string | null;
  needs_move_to?: number | null;
  stop: number | null;
  stop_distance?: number | null;
  stop_level?: number | null;
  stop_tag?: string | null;
  stop_why?: string | null;
  stop_buffer?: number | null;
  min_stop_distance?: number | null;
  volatility_stop?: boolean;
  win_chance?: number | null;
  win_chance_driftless?: number | null;
  round_trip_charges?: number | null;
  lot_size: number | null;
  t_hold: number;
  hold_mode: HoldMode;
  hold_label?: string;
  candidates: StrikeCandidate[];
  highlight?: string | null;
  closest?: string | null;
  closest_fails?: string[];
  setup_message?: string | null;
  has_setup?: boolean;
}

export interface StrikePicker {
  lot_size: number | null;
  hold_mode: HoldMode;
  hold_modes?: HoldMode[];
  target_mode?: TargetMode | string;
  round_trip_charges?: number;
  t_holds?: Partial<Record<HoldMode, number>>;
  Bullish: StrikePickerSide;
  Bearish: StrikePickerSide;
  Bullish_targets?: Partial<Record<string, StrikePickerSide>>;
  Bearish_targets?: Partial<Record<string, StrikePickerSide>>;
  active: StrikePickerSide;
}

export interface BiasComponent {
  key: string;
  label: string;
  weight: number;
  score: number;
  contribution: number;
  available?: boolean;
}

export interface BiasChanged {
  r1?: number | null;
  s1?: number | null;
  pivot?: number | null;
  max_pain?: number | null;
  pcr_full?: number | null;
  atm_iv?: number | null;
  prev_as_of_ist?: string | null;
  prev_spot?: number | null;
}

export interface OptionsBias {
  score: number;
  label: string;
  direction?: "Bullish" | "Bearish" | null;
  components: BiasComponent[];
  premarket_available: boolean;
  gift_nifty: string;
  changed_since_yesterday?: BiasChanged | null;
  expiry_day?: boolean;
  win_bump?: number;
  as_of?: number;
  as_of_ist?: string;
  spot?: number;
}

export interface OptionsOverview {
  headline: string;
  levels: {
    r1?: number | null;
    r1_dist?: number | null;
    pivot?: number | null;
    pivot_dist?: number | null;
    pivot_label?: string | null;
    pivot_read?: string | null;
    s1?: number | null;
    s1_dist?: number | null;
    max_pain?: number | null;
    max_pain_dist?: number | null;
  };
  expected_range: {
    market_priced?: (ExpectedMoveBand & { label?: string }) | null;
    range_move?: number | null;
    one_sigma?: (ExpectedMoveBand & { label?: string; available?: boolean; info_only?: boolean }) | null;
  };
  picker: string;
}

export interface OptionsChainResponse {
  available: boolean;
  reason?: string | null;
  expiry: string;
  expiries?: string[];
  as_of: number;
  as_of_ist: string;
  status: OptionsStatus;
  from_snapshot?: boolean;
  spot?: number;
  atm?: number;
  pcr?: number | null;
  total_call_oi?: number;
  total_put_oi?: number;
  resistance?: OptionsLevel[];
  support?: OptionsLevel[];
  pivot?: OptionsPivot | null;
  max_pain?: OptionsMaxPain | null;
  major_walls?: OptionsMajorWalls;
  pcrs?: OptionsPcrs;
  /** True when full / near / ΔOI reads disagree (Phase 4 bias score will weight them). */
  pcr_mixed_signals?: boolean;
  buildup_analysis?: BuildupAnalysis;
  near_atm_strikes?: number[];
  rows: OptionsRow[];
  max_oi?: number;
  max_oi_chg?: number;
  iv_panel?: IvPanel | null;
  expected_move?: ExpectedMove | null;
  strike_picker?: StrikePicker | null;
  bias?: OptionsBias | null;
  overview?: OptionsOverview | null;
}

export interface OptionsTrendPoint {
  t: number;
  as_of_ist?: string;
  spot?: number | null;
  pcr_full?: number | null;
  pcr_near_atm?: number | null;
  expiry?: string;
}

export interface OptionsTrendsResponse {
  day: string;
  available: boolean;
  message?: string | null;
  points: OptionsTrendPoint[];
}

export function fetchOptionsChain(signal?: AbortSignal, expiry?: string): Promise<OptionsChainResponse> {
  const q = expiry ? `?expiry=${encodeURIComponent(expiry)}` : "";
  return getJson<OptionsChainResponse>(`/api/options/chain${q}`, signal);
}

export function fetchOptionsTrends(signal?: AbortSignal, expiry?: string): Promise<OptionsTrendsResponse> {
  const q = expiry ? `?expiry=${encodeURIComponent(expiry)}` : "";
  return getJson<OptionsTrendsResponse>(`/api/options/trends${q}`, signal);
}

export function optionsErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 503) return err.message || "Data token missing or expired";
    if (err.status === 429) return "Upstox rate limit — try again in a moment";
    if (err.status === 502) return err.message || "Option chain request failed";
    return err.message;
  }
  if (err instanceof Error) return err.message;
  return String(err);
}
