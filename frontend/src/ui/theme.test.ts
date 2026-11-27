import { afterEach, describe, expect, it, vi } from "vitest";

import {
  THEME_KEY,
  applyDocumentTheme,
  candleColors,
  chartPalette,
  chartThemeOptions,
  loadTheme,
  saveTheme,
} from "./theme";

describe("app theme", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it("light matches the Learn look: white background, light grid, dark text, green/red candles", () => {
    const light = chartPalette("light");
    expect(light).toMatchObject({ background: "#ffffff", grid: "#f3f4f6", text: "#374151", up: "#16a34a", down: "#dc2626" });
    const dark = chartPalette("dark");
    expect(dark).toMatchObject({ background: "#0b0e14", text: "#d1d4dc", up: "#26a69a", down: "#ef5350" });
  });

  it("chart options carry the palette and never show the TradingView logo", () => {
    for (const theme of ["light", "dark"] as const) {
      const options = chartThemeOptions(theme);
      const palette = chartPalette(theme);
      expect(options.layout?.attributionLogo).toBe(false);
      expect(options.layout?.background).toEqual({ type: "solid", color: palette.background });
      expect(options.layout?.textColor).toBe(palette.text);
      expect(options.grid?.vertLines?.color).toBe(palette.grid);
      expect(options.grid?.horzLines?.color).toBe(palette.grid);
      expect(options.rightPriceScale?.borderColor).toBe(palette.border);
      expect(options.timeScale?.borderColor).toBe(palette.border);
    }
    expect(chartThemeOptions("light", { transparent: true }).layout?.background).toEqual({ type: "solid", color: "transparent" });
  });

  it("candle colours follow the theme", () => {
    expect(candleColors("light")).toEqual({ upColor: "#16a34a", downColor: "#dc2626", wickUpColor: "#16a34a", wickDownColor: "#dc2626" });
    expect(candleColors("dark").upColor).toBe("#26a69a");
  });

  it("defaults to light and remembers the choice", () => {
    expect(loadTheme()).toBe("light");
    saveTheme("dark");
    expect(localStorage.getItem(THEME_KEY)).toBe("dark");
    expect(loadTheme()).toBe("dark");
    localStorage.setItem(THEME_KEY, "purple");
    expect(loadTheme()).toBe("light");
  });

  it("falls back to light when storage throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    expect(loadTheme()).toBe("light");
    expect(() => saveTheme("dark")).not.toThrow();
  });

  it("marks the document so the CSS variables switch", () => {
    applyDocumentTheme("light");
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(document.documentElement.style.colorScheme).toBe("light");
    applyDocumentTheme("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
  });
});
