/**
 * One app theme for every chart and panel. Light matches the Learn tab look.
 * The choice is kept in this browser. The Learn tab keeps its own toggle.
 */

import { ColorType, type ChartOptions, type DeepPartial } from "lightweight-charts";

export type AppTheme = "light" | "dark";

export const THEME_KEY = "tradeboss-theme";

export interface ChartPalette {
  background: string;
  text: string;
  grid: string;
  border: string;
  separatorHover: string;
  up: string;
  down: string;
}

const PALETTES: Record<AppTheme, ChartPalette> = {
  light: {
    background: "#ffffff",
    text: "#374151",
    grid: "#f3f4f6",
    border: "#e5e7eb",
    separatorHover: "#cbd5e1",
    up: "#16a34a",
    down: "#dc2626",
  },
  dark: {
    background: "#0b0e14",
    text: "#d1d4dc",
    grid: "#1a1f2b",
    border: "#2a2e39",
    separatorHover: "#3b4252",
    up: "#26a69a",
    down: "#ef5350",
  },
};

export function chartPalette(theme: AppTheme): ChartPalette {
  return PALETTES[theme];
}

/** Colours and chrome for createChart() / applyOptions(). The TradingView logo is always off. */
export function chartThemeOptions(theme: AppTheme, opts: { transparent?: boolean } = {}): DeepPartial<ChartOptions> {
  const p = chartPalette(theme);
  return {
    layout: {
      background: { type: ColorType.Solid, color: opts.transparent ? "transparent" : p.background },
      textColor: p.text,
      attributionLogo: false,
      panes: { separatorColor: p.border, separatorHoverColor: p.separatorHover },
    },
    grid: { vertLines: { color: p.grid }, horzLines: { color: p.grid } },
    rightPriceScale: { borderColor: p.border },
    timeScale: { borderColor: p.border },
  };
}

export function candleColors(theme: AppTheme): { upColor: string; downColor: string; wickUpColor: string; wickDownColor: string } {
  const p = chartPalette(theme);
  return { upColor: p.up, downColor: p.down, wickUpColor: p.up, wickDownColor: p.down };
}

export function loadTheme(): AppTheme {
  try {
    const value = localStorage.getItem(THEME_KEY);
    return value === "dark" ? "dark" : "light";
  } catch {
    return "light";
  }
}

export function saveTheme(theme: AppTheme): void {
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    /* the theme still switches; it is just not remembered */
  }
}

/** Sets data-theme on <html>; the CSS variables in index.css follow it. */
export function applyDocumentTheme(theme: AppTheme): void {
  if (typeof document === "undefined") return;
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
}
