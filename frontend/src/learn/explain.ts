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
};

export function explain(id: string): string {
  return EXPLAIN[id] ?? "A level read from the closed candles.";
}
