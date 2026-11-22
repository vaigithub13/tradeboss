import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../chart/ChartView", () => ({ ChartView: () => <div data-testid="nifty-chart" /> }));
vi.mock("../panels/SensexPanel", () => ({ SensexPanel: () => <div data-testid="sensex-panel" /> }));
vi.mock("../options/OptionsTab", () => ({ OptionsTab: () => <div data-testid="options-tab" /> }));
vi.mock("../paper/PaperDashboard", () => ({ PaperDashboard: () => <div data-testid="paper-dashboard" /> }));
vi.mock("../live/client", () => ({
  LiveClient: class {
    start() {}
    stop() {}
    setViews() {}
  },
}));

import App from "../App";
import { useLiveStore } from "../store/liveStore";

const live = useLiveStore.getState();

describe("scanner tab switch", () => {
  afterEach(() => {
    cleanup();
    localStorage.clear();
    useLiveStore.setState({ start: live.start, stop: live.stop, setViews: live.setViews });
  });

  it("hides the Nifty chart and shows the scanner pane", async () => {
    useLiveStore.setState({ start: () => {}, stop: () => {}, setViews: () => {} });
    const user = userEvent.setup();
    render(<App />);
    expect(screen.getByTestId("chart-pane").className).not.toContain("invisible");
    await user.click(screen.getByTestId("tab-scanner"));
    expect(screen.getByTestId("chart-pane").className).toContain("invisible");
    expect(screen.getByTestId("scanner-pane")).toBeInTheDocument();
    expect(screen.queryByTestId("options-pane")).not.toBeInTheDocument();
    expect(screen.queryByTestId("paper-pane")).not.toBeInTheDocument();
  });
});
