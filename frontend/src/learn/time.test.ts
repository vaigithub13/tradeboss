import { describe, expect, it } from "vitest";

import { fromIstInput, istEpoch, learnAxisTime, toIstInput } from "./time";

describe("Learn chart IST axis", () => {
  it("reads the Nifty session as 09:15 and 15:30", () => {
    const open = istEpoch(2026, 11, 26, 9, 15);
    const close = istEpoch(2026, 11, 26, 15, 30);
    expect(learnAxisTime(open)).toBe("09:15");
    expect(learnAxisTime(close)).toBe("15:30");
    expect(toIstInput(open)).toBe("2026-11-26T09:15");
    expect(fromIstInput("2026-11-26T15:30")).toBe(close);
  });
});
