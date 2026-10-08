import { afterEach, describe, expect, it, vi } from "vitest";

import { clampSize, dragSize, readSize, writeSize } from "./storedSize";

function fakeStorage(): Storage {
  const data = new Map<string, string>();
  return {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
    removeItem: (k: string) => void data.delete(k),
    clear: () => data.clear(),
    key: () => null,
    length: 0,
  } as Storage;
}

describe("stored sizes", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("remembers a size and clamps what comes back", () => {
    vi.stubGlobal("window", { localStorage: fakeStorage() });
    expect(readSize("w", 384, 280, 900)).toBe(384);
    writeSize("w", 512.4);
    expect(readSize("w", 384, 280, 900)).toBe(512);
    writeSize("w", 5000);
    expect(readSize("w", 384, 280, 900)).toBe(900);
  });

  it("falls back when storage throws", () => {
    vi.stubGlobal("window", { localStorage: { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); } } });
    expect(readSize("w", 384, 280, 900)).toBe(384);
    expect(() => writeSize("w", 400)).not.toThrow();
  });

  it("drags an edge within limits", () => {
    expect(dragSize(384, 100, -1, 280, 900)).toBe(284); // left edge of a right panel: dragging right shrinks it
    expect(dragSize(320, -200, -1, 160, 800)).toBe(520); // top edge of a bottom drawer: dragging up grows it
    expect(clampSize(10, 160, 800)).toBe(160);
  });
});
