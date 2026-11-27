import { describe, expect, it } from "vitest";

import { crtMarks, CRT_CYAN } from "./crtMarks";
import type { LearnBar } from "./structure";
import { istEpoch } from "./time";

const M5 = 300;
const t = (hour: number, minute: number) => istEpoch(2026, 11, 26, hour, minute);

function bars(): LearnBar[] {
  const out: LearnBar[] = [];
  for (let at = t(9, 15), i = 0; at < t(13, 0); at += M5, i += 1) {
    let bar: [number, number, number, number] = [120, 122, 118, 120];
    if (i === 3) bar = [120, 140, 119, 121];
    if (i === 9) bar = [120, 121, 100, 119];
    if (i >= 15 && i < 30) bar = i === 19 ? [104, 106, 95, 103] : [108, 110, 104, 108];
    if (i >= 30) bar = i === 36 ? [130, 141, 129, 139] : [125, 127, 123, 125];
    out.push({ time: at, open: bar[0], high: bar[1], low: bar[2], close: bar[3] });
  }
  return out;
}

describe("CRT chart marks", () => {
  it("draws cyan 75m CRT high, low and 50% lines and a dashed target", () => {
    const m5 = bars();
    const marks = crtMarks(m5, t(13, 0), m5, 300);
    const level = marks.lines.filter((line) => line.kind === "crt_lvl");
    expect(level.map((line) => line.label)).toEqual(expect.arrayContaining(["75m CRT high", "75m CRT low", "75m CRT 50%"]));
    expect(level.every((line) => line.color === CRT_CYAN)).toBe(true);
    expect(level.find((line) => line.label === "75m CRT 50%")!.dashed).toBe(true);
    const target = marks.lines.find((line) => line.kind === "crt_target")!;
    expect(target).toMatchObject({ price: 140, dashed: true, label: "CRT target ✓" });
  });

  it("labels the sweep and the CRT ✓ with a buy-study tooltip", () => {
    const m5 = bars();
    const marks = crtMarks(m5, t(13, 0), m5, 300);
    const sweep = marks.labels.find((label) => label.kind === "crt_sweep")!;
    expect(sweep).toMatchObject({ text: "CRT sweep", price: 95, pos: "below", time: t(10, 50) });
    const ok = marks.labels.find((label) => label.kind === "crt_ok")!;
    expect(ok.text).toBe("CRT ✓ ↑");
    expect(ok.tip).toContain("🟢 buy study");
    expect(ok.tip).toContain("TP 140");
    expect(ok.tip).toContain("study only — not a trade");
  });

  it("writes forming, confirmed and target-reached events", () => {
    const m5 = bars();
    const texts = crtMarks(m5, t(13, 0), m5, 300).events.map((event) => event.text);
    expect(texts).toContain("75m CRT forming: previous low swept");
    expect(texts.some((text) => text.startsWith("75m CRT ✓"))).toBe(true);
    expect(texts).toContain("75m CRT target reached");
  });

  it("shows only what has closed: no ✓ before the 11:45 close", () => {
    const m5 = bars();
    const early = crtMarks(m5, t(11, 40), m5, 300);
    expect(early.labels.some((label) => label.kind === "crt_ok")).toBe(false);
    expect(early.labels.some((label) => label.kind === "crt_sweep")).toBe(true);
    expect(early.lines.some((line) => line.kind === "crt_target")).toBe(false);
  });

  it("leaves 75m CRT off charts above 15 minutes", () => {
    const m5 = bars();
    const marks = crtMarks(m5, t(13, 0), m5, 3600);
    expect(marks.lines.some((line) => line.label.startsWith("75m"))).toBe(false);
  });
});
