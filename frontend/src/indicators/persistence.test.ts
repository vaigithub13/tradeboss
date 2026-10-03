import { describe, expect, it } from "vitest";

import { createInstance } from "./catalog";
import { STORAGE_KEY, loadItems, saveItems } from "./persistence";

class MemoryStorage {
  data = new Map<string, string>();
  getItem(k: string): string | null {
    return this.data.get(k) ?? null;
  }
  setItem(k: string, v: string): void {
    this.data.set(k, v);
  }
}

describe("indicator settings persistence", () => {
  it("survives a reload: what was saved is what is loaded (params, colours, visibility, copies)", () => {
    const a = { ...createInstance("ema", []), params: { length: 20, source: "close" } };
    const b = { ...createInstance("ema", [a]), params: { length: 50, source: "hl2" }, visible: false };
    const c = { ...createInstance("bb", []), colors: { basis: "#010203", band: "#0a0b0c" } };
    const storage = new MemoryStorage();
    saveItems([a, b, c], storage);
    expect(loadItems(storage)).toEqual([a, b, c]);
  });

  it("empty storage gives no indicators", () => {
    expect(loadItems(new MemoryStorage())).toEqual([]);
    expect(loadItems(null)).toEqual([]);
  });

  it("corrupted storage never throws", () => {
    const storage = new MemoryStorage();
    storage.setItem(STORAGE_KEY, "{not json");
    expect(loadItems(storage)).toEqual([]);
    storage.setItem(STORAGE_KEY, JSON.stringify({ items: "oops" }));
    expect(loadItems(storage)).toEqual([]);
    storage.setItem(STORAGE_KEY, JSON.stringify(42));
    expect(loadItems(storage)).toEqual([]);
  });

  it("a failing setItem (quota / private mode) is swallowed", () => {
    const broken = {
      setItem: () => {
        throw new Error("quota");
      },
    };
    expect(() => saveItems([createInstance("ema", [])], broken)).not.toThrow();
  });
});
