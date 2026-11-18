/**
 * Which chart a shared component (ChartView, IndicatorsMenu, SessionsMenu, DrawToolbar) talks to.
 * The default is the main Nifty chart; the Sensex tab wraps its subtree in `SENSEX_SCOPE`, so the
 * very same components read and write the Sensex stores instead.
 */
import { createContext, useContext, type ReactNode } from "react";

import { useChartStore, useSensexChartStore, type ChartStoreHook } from "./chartStore";
import { useIndicatorStore, useSensexIndicatorStore, type IndicatorStoreHook } from "./indicatorStore";

export interface ChartScope {
  id: "nifty" | "sensex";
  chart: ChartStoreHook;
  indicators: IndicatorStoreHook;
}

export const NIFTY_SCOPE: ChartScope = { id: "nifty", chart: useChartStore, indicators: useIndicatorStore };
export const SENSEX_SCOPE: ChartScope = { id: "sensex", chart: useSensexChartStore, indicators: useSensexIndicatorStore };

const ChartScopeContext = createContext<ChartScope>(NIFTY_SCOPE);

export function ChartScopeProvider({ scope, children }: { scope: ChartScope; children: ReactNode }) {
  return <ChartScopeContext.Provider value={scope}>{children}</ChartScopeContext.Provider>;
}

export function useChartScope(): ChartScope {
  return useContext(ChartScopeContext);
}
