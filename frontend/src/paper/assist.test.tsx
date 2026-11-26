import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AssistBanner, AssistEntryDialog, AssistTrailDialog, PracticeRead } from "./AssistHost";
import {
  assistEvents,
  guardFrom,
  maxLoss,
  premiumBracket,
  reminderDue,
  scorePractice,
  snapTick,
  storyDue,
  userTrailStop,
  type AssistMemory,
  type AssistSettings,
  type AssistSlot,
  type Guard,
} from "./assist";

const DAY = "2026-11-27";
const T = 1_791_778_860_000;

function signal(at = T) {
  return {
    time: at / 1000 - 300, decided_at_ms: at, side: "BUY", status: "filled", symbol: "NIFTY 24500 CE 27 NOV 26",
    fill_price: 200, index_price: 24510, source: "live",
  };
}

function slot(over: Partial<AssistSlot> = {}): AssistSlot {
  return {
    slot: "4", state: "running", day: DAY, source: "live", signals: [], trades: [], ...over,
  };
}

function poll(steps: AssistSlot[], enabled = true) {
  let memory: AssistMemory | undefined;
  let events: ReturnType<typeof assistEvents>["events"] = [];
  for (const step of steps) ({ events, memory } = assistEvents(memory, step, enabled));
  return events;
}

const settings: Pick<AssistSettings, "max_trades" | "day_loss" | "account_pause" | "start_balance"> = {
  max_trades: 3, day_loss: 3000, account_pause: 10000, start_balance: null,
};

describe("slot 4 assist", () => {
  afterEach(() => cleanup());

  it("fires a popup once per signal, and a later exit once", () => {
    const filled = slot({ signals: [signal()] });
    expect(poll([slot(), filled])).toEqual([
      expect.objectContaining({ kind: "entry", slot: "4", contract: "NIFTY 24500 CE 27 NOV 26", decidedAtMs: T }),
    ]);
    expect(poll([slot(), filled, filled])).toEqual([]);
    const closed = slot({
      signals: [signal()],
      trades: [{ symbol: "NIFTY 24500 CE 27 NOV 26", exit_reason: "premium_stop", exit_price: 160, entry_at_ms: T, exit_at_ms: T + 60_000, net: -2600 }],
    });
    expect(poll([slot(), filled, closed]).map((e) => e.kind)).toEqual(["exit"]);
    expect(poll([closed])).toEqual([]); // already on the book when the page opened
  });

  it("stays quiet while the assist is off, and does not replay those signals when it is turned on", () => {
    const filled = slot({ signals: [signal()] });
    expect(poll([slot(), filled], false)).toEqual([]);
    let memory: AssistMemory | undefined;
    ({ memory } = assistEvents(memory, slot(), false));
    ({ memory } = assistEvents(memory, filled, false));
    expect(assistEvents(memory, filled, true).events).toEqual([]);
  });

  it("prices the stop and target from the fill, and the guard banner", () => {
    expect(premiumBracket(100)).toEqual([80, 140]);
    expect(premiumBracket(100.05)).toEqual([80.04, 140.07]);
    expect(snapTick(100.03)).toBe(100.05);
    expect(maxLoss(100, 80, 75, 50)).toBe(1550);
    const stop = guardFrom(
      [{ key: "4:d:1", kind: "taken", slot: "4", day: DAY, fill_time: 1, net: -3000, exit_time: 2 }],
      settings,
      DAY,
    );
    expect(stop.stop_for_today).toBe(true);
    expect(stop.pause).toBe(false);
    const cap = guardFrom(
      [1, 2, 3].map((n) => ({ key: String(n), kind: "taken" as const, slot: "4", day: DAY, fill_time: n, net: -10, exit_time: n })),
      settings,
      DAY,
    );
    expect(cap.at_trade_cap).toBe(true);
    const skipped = guardFrom(
      [{ key: "s", kind: "skipped", slot: "4", day: DAY, fill_time: 1 }],
      settings,
      DAY,
    );
    expect(skipped.trades_today).toBe(0);
    expect(reminderDue(15 * 60 + 10, 1, false)).toBe(true);
    expect(storyDue(15 * 60 + 30)).toBe(true);
  });

  it("shows the stop banner and the ticket, and logs a take once", async () => {
    const guard: Guard = {
      trades_today: 1, max_trades: 3, at_trade_cap: false, pnl_today: -3000, day_loss: 3000,
      stop_for_today: true, open_trades: 0, start_balance: 100000, account_pnl: -10000, equity: 90000,
      account_pause: 10000, pause: true, needs_start_balance: false,
    };
    render(<AssistBanner guard={guard} enabled onToggleSettings={() => undefined} />);
    expect(screen.getByTestId("assist-stop").textContent).toMatch(/STOP for today/);
    expect(screen.getByTestId("assist-pause").textContent).toMatch(/Pause/);
    cleanup();

    const onTook = vi.fn();
    render(
      <AssistEntryDialog
        event={{ kind: "entry", slot: "4", day: DAY, contract: "NIFTY 24500 CE 27 NOV 26", decidedAtMs: T, side: "BUY" }}
        ticket={{
          key: "4:2026-11-27:1", slot: "4", day: DAY, fill_time: 1, decided_at_ms: T, time: "10:05",
          action: "BUY", option_side: "CE", signal_side: "BUY", contract: "NIFTY 24500 CE 27 NOV 26",
          bid: 199.5, ask: 200.03, ltp: 199.8, suggested_limit: 200.05, lots: 1, lot_size: 65, quantity: 65,
          stop: 160.04, target: 280.07, stop_frac: 0.2, target_frac: 0.4, charges: 40, max_loss: 2640.65,
          paper_fill: 200, why: "5m price 24,510.00 crossed the 20-bar channel high 24,508.00 set at 10:00; channel low 24,400.00; Nifty above VWAP 24,450.00.",
          chart: {
            candles: [
              { time: 1, open: 100, high: 110, low: 90, close: 105 },
              { time: 2, open: 105, high: 112, low: 100, close: 111 },
            ],
            channel_high: 110, channel_low: 90, signal_time: 2,
          },
          index_price: 24510,
        }}
        busy={false}
        error={null}
        onTook={onTook}
        onSkip={() => undefined}
      />,
    );
    expect(screen.getByTestId("assist-why").textContent).toMatch(/channel high 24,508.00/);
    expect(screen.getByTestId("assist-signal-candle")).toBeTruthy();
    expect(screen.getByText(/From this fill: SL 160.04 · TP 280.07/)).toBeTruthy();
    await userEvent.click(screen.getByTestId("assist-took"));
    expect(onTook).toHaveBeenCalledTimes(1);
    expect(onTook.mock.calls[0]?.[0]).toBeCloseTo(200.05);
  });

  it("shows a slot 5 trail and no +40% target", async () => {
    expect(userTrailStop(110, 116)).toBe(99);
    expect(userTrailStop(100, 116)).toBe(100);
    const upcoming = Array.from({ length: 6 }, () => ({ high: 106, low: 99, close: 110 }));
    expect(scorePractice(100, upcoming, "up", 105)).toEqual({ ready: true, directionHit: true, levelHit: true });
    expect(scorePractice(100, upcoming.slice(0, 3), "up").ready).toBe(false);

    render(
      <AssistEntryDialog
        event={{ kind: "entry", slot: "5", day: DAY, contract: "NIFTY 25000 CE", decidedAtMs: T, side: "BUY" }}
        ticket={{
          key: "5:2026-11-27:1", slot: "5", day: DAY, fill_time: 1, decided_at_ms: T, time: "10:05",
          action: "BUY", option_side: "CE", signal_side: "BUY", contract: "NIFTY 25000 CE",
          bid: 199.5, ask: 200.03, ltp: 199.8, suggested_limit: 200.05, lots: 1, lot_size: 65, quantity: 65,
          stop: 180.05, target: null, stop_frac: 0.1, target_frac: null, charges: 40, max_loss: 1340,
          paper_fill: 200, trailing: true, exit_note: "exit by trailing stop", market: "NIFTY",
          why: "DD long confirmations: close above Supertrend.",
          price_action: { text: "Structure HH/HL." },
          chart: null, index_price: 24510,
        }}
        busy={false}
        error={null}
        onTook={() => undefined}
        onSkip={() => undefined}
      />,
    );
    expect(screen.getByText(/NIFTY slot 5/)).toBeTruthy();
    expect(screen.getByTestId("assist-trail-note").textContent).toMatch(/exit by trailing stop/);
    expect(screen.queryByText(/TP /)).toBeNull();
    expect(screen.getByTestId("assist-pa").textContent).toMatch(/HH\/HL/);
    cleanup();

    const onUpdated = vi.fn();
    render(
      <AssistTrailDialog
        notice={{ key: "5:d:1", slot: "5", market: "NIFTY", contract: "NIFTY 25000 CE", paper_stop: 100, real_stop: 99, moved_at: 1, text: "Move SL to ₹99.00" }}
        busy={false}
        error={null}
        onUpdated={onUpdated}
      />,
    );
    expect(screen.getByTestId("assist-trail-text").textContent).toBe("Move SL to ₹99.00");
    await userEvent.click(screen.getByTestId("assist-trail-updated"));
    expect(onUpdated).toHaveBeenCalledTimes(1);
  });

  it("saves a practice read before it reveals the next six candles", async () => {
    const bars = Array.from({ length: 18 }, (_, i) => ({
      time: 1_000 + i * 60, open: 100, high: i >= 12 ? 112 : 101, low: 99, close: i >= 12 ? 110 : 100,
    }));
    const onPersist = vi.fn().mockResolvedValue({ saved: true });
    render(<PracticeRead bars={bars} onPersist={onPersist} />);
    const reveal = screen.getByTestId("assist-practice-reveal");
    expect(reveal).toHaveProperty("disabled", true);
    await userEvent.type(screen.getByLabelText("Reason"), "higher low");
    await userEvent.click(screen.getByTestId("assist-practice-save"));
    expect(onPersist).toHaveBeenCalledWith(expect.objectContaining({ direction: "up", tag: "trend pullback", reason: "higher low" }));
    await waitFor(() => expect(screen.getByTestId("assist-practice-reveal")).toHaveProperty("disabled", false));
    await userEvent.click(screen.getByTestId("assist-practice-reveal"));
    expect(screen.getByTestId("assist-practice-score").textContent).toMatch(/direction hit/);
  });
});
