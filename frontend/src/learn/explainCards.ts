/** Teach cards. Wording follows the AurumBoss Learn cards, written for the Nifty session. */

export interface ExplainCard {
  title: string;
  what: string;
  why: string;
  next: string;
}

export const EXPLAIN_CARDS: Record<string, ExplainCard> = {
  session: {
    title: "Nifty session",
    what: "Opening hour is 09:15–10:15 IST, midday is 10:15–13:30, and the closing drive is 13:30–15:30.",
    why: "The open often builds the balance. The middle of the day trends or ranges. The close often delivers the last push.",
    next: "Ask which part of the session you are in before you read a sweep or a break.",
  },
  amd_a: {
    title: "A — Accumulation",
    what: "The opening balance, from 09:15 until the range expands or 10:15, whichever comes first.",
    why: "Stops collect just outside that high and low. They are the first liquidity the rest of the day can raid.",
    next: "Mark the high and low. Wait. Do not treat the opening hour itself as a finished read.",
  },
  amd_m: {
    title: "M — Manipulation",
    what: "Price traded beyond the opening balance, or beyond PDH or PDL, and a candle closed back inside.",
    why: "A raid of stops that fails to stay outside often comes just before the real move the other way.",
    next: "Watch for a CHoCH in the new direction, then a pullback into a fresh gap or order block. The stop belongs beyond the sweep.",
  },
  amd_d: {
    title: "D — Distribution",
    what: "After the sweep, a candle closed beyond the opposite side of the opening balance.",
    why: "That close says the sweep was the trap and the session is now expanding the other way.",
    next: "A late chase has a poor reward. Prefer a pullback into a gap, with the next liquidity as the target.",
  },
  hh: {
    title: "HH — Higher high",
    what: "A swing high above the previous swing high.",
    why: "Higher highs with higher lows are an uptrend.",
    next: "In an uptrend, study pullbacks to higher lows rather than selling the high.",
  },
  hl: {
    title: "HL — Higher low",
    what: "A swing low above the previous swing low.",
    why: "Buyers are stepping in earlier. That is a healthy uptrend.",
    next: "A close below the last higher low is the first warning the uptrend may be ending.",
  },
  lh: {
    title: "LH — Lower high",
    what: "A swing high below the previous swing high.",
    why: "Sellers are stepping in earlier. That is part of a downtrend.",
    next: "A close above the last lower high would be a bullish change of character.",
  },
  ll: {
    title: "LL — Lower low",
    what: "A swing low below the previous swing low.",
    why: "Lower highs with lower lows are a downtrend.",
    next: "In a downtrend, study pullbacks into lower highs.",
  },
  swing_high: {
    title: "Swing high",
    what: "The first swing high in view. There is no earlier high to compare it with.",
    why: "Swings are the skeleton of market structure.",
    next: "Wait for the next swing high to see if it is higher or lower.",
  },
  swing_low: {
    title: "Swing low",
    what: "The first swing low in view.",
    why: "Swings are the skeleton of market structure.",
    next: "Wait for the next swing low to see if it is higher or lower.",
  },
  bos: {
    title: "BOS — Break of structure",
    what: "A candle closed beyond the last swing in the direction of the current trend.",
    why: "It says the trend is continuing. A wick alone does not count.",
    next: "Wait for a pullback, often into the gap or order block that caused the break.",
  },
  choch: {
    title: "CHoCH — Change of character",
    what: "The first close beyond a swing against the current trend.",
    why: "It is the first sign the trend may be turning. After a sweep, it is the usual confirmation.",
    next: "It is not an order. Wait for a pullback and a reaction before you even study an entry.",
  },
  pdh: {
    title: "PDH — Previous day high",
    what: "Yesterday's highest price on this chart.",
    why: "Stops and breakout orders often sit just above it.",
    next: "Watch for a wick through it that closes back below, or a clean close and a retest.",
  },
  pdl: {
    title: "PDL — Previous day low",
    what: "Yesterday's lowest price on this chart.",
    why: "Stops of buyers often rest just below it.",
    next: "A sweep that closes back above is a common long study. If it fails, stay patient.",
  },
  pwh: {
    title: "PWH — Previous week high",
    what: "Last week's high.",
    why: "A larger pool of liquidity than the previous day. Reactions here can be strong.",
    next: "Treat it as a major target or a place price may turn.",
  },
  pwl: {
    title: "PWL — Previous week low",
    what: "Last week's low.",
    why: "A larger pool of liquidity than the previous day.",
    next: "Treat it as a major target or a place price may turn.",
  },
  or_h: {
    title: "Opening-range high",
    what: "The top of today's 09:15–09:45 IST opening range.",
    why: "It is a near liquidity edge from the first half hour.",
    next: "A sweep that closes back inside can be the manipulation. A close beyond it can start the real move.",
  },
  or_l: {
    title: "Opening-range low",
    what: "The bottom of today's 09:15–09:45 IST opening range.",
    why: "Stops often sit just under the first half hour.",
    next: "A sweep that closes back above is a bullish study. A close below it is acceptance.",
  },
  eqh: {
    title: "EQH — Equal highs",
    what: "Two swing highs within a tenth of the 14-bar average range.",
    why: "Traders see a double top and park stops just above it.",
    next: "Price often runs equal highs before it turns. Do not treat the level itself as a safe stop.",
  },
  eql: {
    title: "EQL — Equal lows",
    what: "Two swing lows within a tenth of the 14-bar average range.",
    why: "Stops cluster just below them.",
    next: "A sweep that closes back above can be a long study.",
  },
  sweep: {
    title: "Liquidity sweep",
    what: "Price traded through a level and the candle, or the next candle, closed back on the original side.",
    why: "Stops were triggered and price was rejected. The poke was a trap.",
    next: "Look for a change of character the other way. If price goes back through the sweep, the read failed.",
  },
  level_break: {
    title: "Level broken",
    what: "A candle closed through the level. A wick alone does not count.",
    why: "Acceptance beyond the level. The old level often flips into support or resistance.",
    next: "Watch for a retest from the other side.",
  },
  fvg_bull: {
    title: "Bullish fair value gap",
    what: "A three-candle gap: candle 3's low is above candle 1's high, and the gap is at least a tenth of the average range.",
    why: "Price moved up so fast it left an imbalance. It often returns into that gap.",
    next: "In a bullish read, a pullback into the gap is the area to study. A full fill means it has been used up.",
  },
  fvg_bear: {
    title: "Bearish fair value gap",
    what: "Candle 3's high is below candle 1's low, and the gap is at least a tenth of the average range.",
    why: "A fast drop left an imbalance. Price often returns into it.",
    next: "In a bearish read, look for a rejection inside the gap.",
  },
  ob_bull: {
    title: "Bullish order block",
    what: "The last down candle before a close that broke structure upward.",
    why: "It is the last opposing candle before the impulse. Price often reacts if it comes back.",
    next: "Use it with a gap and a liquidity level. An order block on its own is a weak read.",
  },
  ob_bear: {
    title: "Bearish order block",
    what: "The last up candle before a close that broke structure downward.",
    why: "It is the last opposing candle before the impulse down.",
    next: "Watch for a rejection if price trades back up into it.",
  },
  engulf_bull: {
    title: "Bullish engulfing",
    what: "An up candle whose body covers the previous down candle's body.",
    why: "Buyers took the prior body in one candle. It matters most at a key level after a sweep.",
    next: "Study the high as the line that would confirm, and the low as the line that would negate it.",
  },
  engulf_bear: {
    title: "Bearish engulfing",
    what: "A down candle whose body covers the previous up candle's body.",
    why: "Sellers took the prior body. It matters most at a key level after a sweep of highs.",
    next: "Study the low as the line that would confirm, and the high as the line that would negate it.",
  },
  pin_bull: {
    title: "Bullish pin bar",
    what: "A long lower wick, at least twice the body and most of the range, with a small upper wick.",
    why: "Price was pushed down and rejected.",
    next: "It matters most at a sweep of a low or inside a bullish gap.",
  },
  pin_bear: {
    title: "Bearish pin bar",
    what: "A long upper wick, at least twice the body and most of the range, with a small lower wick.",
    why: "Price was pushed up and rejected.",
    next: "It matters most at a sweep of a high or inside a bearish gap.",
  },
  doji: {
    title: "Doji",
    what: "Open and close almost equal, a body no bigger than a tenth of the range.",
    why: "Indecision. At a key level it can be the pause before a turn.",
    next: "Wait for the next closed candle to show a direction.",
  },
  inside: {
    title: "Inside bar",
    what: "A candle whose high and low are both inside the previous candle.",
    why: "Compression. The next push often leaves the mother candle.",
    next: "Watch a close beyond the previous candle's high or low.",
  },
  round: {
    title: "Round number",
    what: "A price ending in 50 or 00. The darker lines are every 100 points.",
    why: "Orders and stops cluster on obvious numbers.",
    next: "Treat 100-point numbers as stronger than the 50s between them.",
  },
  event: {
    title: "Calendar event",
    what: "A dated item from the Learn calendar: a holiday, an expiry, or a release such as FOMC.",
    why: "The session often behaves differently on an expiry day or around a scheduled release. An unverified date still needs you to check the source.",
    next: "Read the source in the calendar file before you treat the date as settled. Edit events.json to correct it.",
  },
  signal: {
    title: "Watch-only signal",
    what: "A scored box: entry, stop, and target on a closed 5-minute candle. It is not an order and it is not a paper slot.",
    why: "Later closed candles mark it TP, SL, or expired. If one candle trades both the stop and the target, the stop is counted.",
    next: "Read the reason. One signal per setup per day. Nothing here is sent anywhere.",
  },
  plan: {
    title: "Plan, still waiting",
    what: "A setup drawn before price gets there. Waiting means price is outside the zone. In zone means each 5-minute close is being watched.",
    why: "The plan says what would confirm it and what would cancel it, so the chart is read before the move.",
    next: "A plan is not a signal until the confirming candle closes. After 15:00 a new one is not taken.",
  },
  note: {
    title: "My note",
    what: "A note you placed on a candle.",
    why: "Writing the read before the move is how the chart teaches you.",
    next: "Compare it later with what the closed candles actually did.",
  },
};

export function explainTip(kind: string): string {
  const card = explainCard(kind);
  const sentence = card.what.split(". ")[0] ?? card.what;
  return `${card.title}. ${sentence}.`;
}

export function explainCard(kind: string): ExplainCard {
  return EXPLAIN_CARDS[kind] ?? {
    title: kind,
    what: "A mark read from the closed candles.",
    why: "It is a study mark, not a signal and not an order.",
    next: "Click it and read the four lines before you decide what it means.",
  };
}
