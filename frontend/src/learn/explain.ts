/** Fixed one-line explanations. The text does not change with the market. */

export const EXPLAIN: Record<string, string> = {
  H: "The first swing high in this window.",
  HH: "A swing high above the previous swing high.",
  LH: "A swing high below the previous swing high.",
  L: "The first swing low in this window.",
  HL: "A swing low above the previous swing low.",
  LL: "A swing low below the previous swing low.",
  bos: "Close beyond the last swing in the trend direction.",
  choch: "First close beyond the last opposite swing. The trend has changed.",
  Uptrend: "A break of structure in the last swings was upward.",
  Downtrend: "A break of structure in the last swings was downward.",
  Range: "No break of structure in the last swings.",
  pdh: "Previous day's high.",
  pdl: "Previous day's low.",
  prevClose: "Previous day's last close.",
  week: "Previous week's high or low.",
  cpr: "Central pivot range from the previous day: pivot, top, and bottom.",
  round: "A round number, every 100 points.",
  opening: "Opening range, 09:15 to 09:45 IST.",
  EQH: "Two or more swing highs within a tenth of the 14-bar average range.",
  EQL: "Two or more swing lows within a tenth of the 14-bar average range.",
  BSL: "Buy-side liquidity: a prior high sitting above price.",
  SSL: "Sell-side liquidity: a prior low sitting below price.",
  sweep: "Price traded beyond the level and closed back on the original side.",
  Accumulation: "The opening balance, from 09:15 until the range expands or 10:15.",
  Manipulation: "The first sweep of the opening balance, or of yesterday's high or low, that closed back inside.",
  Distribution: "A close through the other side of the opening balance after the sweep.",
  "No AMD today": "The session did not complete a sweep and a close through the other side.",
  textbook: "A learning sketch: entry at the gap or the middle of the move, not a signal.",
  "target hit": "A later closed candle reached the opposite liquidity.",
  "stop hit": "A later closed candle reached the stop beyond the sweep.",
  "neither by 15:15": "Neither the target nor the stop was reached by the 15:15 close.",
};

export function explain(id: string): string {
  return EXPLAIN[id] ?? "A level read from the closed candles.";
}
