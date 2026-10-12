import { afterEach, describe, expect, it, vi } from "vitest";

import { DEFAULT_ALERT_SETTINGS, readAlertSettings } from "./alertStore";

function fakeStorage(seed: Record<string, string> = {}): Storage {
  const data = new Map(Object.entries(seed));
  return {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
    removeItem: (k: string) => void data.delete(k),
    clear: () => data.clear(),
    key: () => null,
    length: 0,
  } as Storage;
}

describe("paper alert settings", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it("default: every slot on, sound off until a click", async () => {
    vi.stubGlobal("window", { localStorage: fakeStorage() });
    expect(readAlertSettings()).toEqual(DEFAULT_ALERT_SETTINGS);
    expect(Object.values(DEFAULT_ALERT_SETTINGS.slots)).toEqual([true, true, true, true]);
    const { useAlertStore } = await import("./alertStore");
    expect(useAlertStore.getState().soundOn).toBe(false);
  });

  it("volume and slot switches are remembered; sound on is not", async () => {
    const storage = fakeStorage();
    vi.stubGlobal("window", { localStorage: storage });
    const first = (await import("./alertStore")).useAlertStore;
    first.getState().setVolume(0.35);
    first.getState().setSlot("2", false);
    first.getState().setSoundOn(true);
    vi.resetModules();
    const again = (await import("./alertStore")).useAlertStore.getState();
    expect(again.volume).toBe(0.35);
    expect(again.slots).toEqual({ "1": true, "2": false, "3": true, "4": true });
    expect(again.soundOn).toBe(false);
  });

  it("falls back to the defaults when storage throws or holds junk", () => {
    vi.stubGlobal("window", { localStorage: { getItem: () => { throw new Error("blocked"); } } });
    expect(readAlertSettings()).toEqual(DEFAULT_ALERT_SETTINGS);
    vi.stubGlobal("window", { localStorage: fakeStorage({ "paper.alerts": '{"volume":"loud"}' }) });
    expect(readAlertSettings().volume).toBe(DEFAULT_ALERT_SETTINGS.volume);
  });
});
