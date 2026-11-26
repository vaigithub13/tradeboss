import { ColorType, type ChartOptions, type DeepPartial, type TickMarkType, type Time } from "lightweight-charts";

import type { Timeframe } from "../api/client";
import { learnTickMarkFormatter, learnTimeFormatter } from "./time";

export type LearnTheme = "light" | "dark";

export interface LearnColors {
  background: string;
  text: string;
  grid: string;
  border: string;
  up: string;
  down: string;
  line: string;
}

export function learnColors(theme: LearnTheme): LearnColors {
  if (theme === "dark") {
    return {
      background: "#0c1016",
      text: "#d1d4dc",
      grid: "#1e2430",
      border: "#2a3142",
      up: "#26a69a",
      down: "#ef5350",
      line: "#5c6b82",
    };
  }
  return {
    background: "#ffffff",
    text: "#1c2330",
    grid: "#e6e8ee",
    border: "#d5d8e0",
    up: "#0b7a43",
    down: "#c62828",
    line: "#5c6b82",
  };
}

export function learnChartOptions(theme: LearnTheme, timeframe: Timeframe = "5m"): DeepPartial<ChartOptions> {
  const colors = learnColors(theme);
  return {
    autoSize: true,
    layout: {
      background: { type: ColorType.Solid, color: colors.background },
      textColor: colors.text,
      fontSize: 10,
      attributionLogo: false,
    },
    grid: { vertLines: { color: colors.grid }, horzLines: { color: colors.grid } },
    rightPriceScale: { borderColor: colors.border },
    timeScale: {
      borderColor: colors.border,
      timeVisible: true,
      secondsVisible: false,
      tickMarkFormatter: (time: Time, type: TickMarkType) => learnTickMarkFormatter(time, type),
    },
    localization: { timeFormatter: learnTimeFormatter(timeframe) },
  };
}
