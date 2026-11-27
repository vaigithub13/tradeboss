/**
 * Node entry for the Learn signal review. Python bundles this file and passes
 * a JSON path of closed 5-minute candles. It prints one signal book and exits.
 */

import { readFileSync } from "node:fs";

import { readSignalBook } from "./signals";
import type { LearnBar } from "./structure";

interface ReviewInput {
  bars: LearnBar[];
  now: number;
  symbol: string;
  left?: number;
  right?: number;
}

const path = process.argv[2];
if (!path) {
  process.stderr.write("usage: reviewMain <bars.json>\n");
  process.exit(1);
}
const input = JSON.parse(readFileSync(path, "utf8")) as ReviewInput;
const book = readSignalBook(input.bars, input.now, input.symbol, {
  left: input.left ?? 3,
  right: input.right ?? 3,
});
process.stdout.write(JSON.stringify(book));
