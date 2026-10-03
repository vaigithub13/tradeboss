import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import "./index.css";
import { useChartStore } from "./store/chartStore";
import { applyBar, useLiveStore } from "./store/liveStore";
import { useIndicatorStore } from "./store/indicatorStore";

// Dev-only handles for manual checks / performance measurements from the browser console.
if (import.meta.env.DEV) {
  (window as unknown as Record<string, unknown>)["__stores"] = { chart: useChartStore, indicators: useIndicatorStore, live: useLiveStore, applyBar };
}

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("Root element #root not found");

createRoot(rootEl).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
