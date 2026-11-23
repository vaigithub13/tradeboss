import { describe, expect, it } from "vitest";

import { coalesce, holdLeader, type LockLike } from "./sharedPoll";

function queueLocks(): LockLike {
  let held = false;
  const waiters: Array<() => void> = [];
  return {
    request: async (_name, _options, callback) => {
      if (held) await new Promise<void>((resolve) => waiters.push(resolve));
      held = true;
      try {
        await callback({});
      } finally {
        held = false;
        waiters.shift()?.();
      }
    },
  };
}

describe("shared poller", () => {
  it("lets one tab poll while the next waits", async () => {
    const locks = queueLocks();
    let started = 0;
    const stopA = holdLeader(
      "health",
      () => {
        started += 1;
        return () => {};
      },
      locks,
    );
    holdLeader(
      "health",
      () => {
        started += 1;
        return () => {};
      },
      locks,
    );
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(started).toBe(1);
    stopA();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(started).toBe(2);
  });

  it("shares one request when two callers overlap", async () => {
    let calls = 0;
    const run = () =>
      coalesce("paper", async () => {
        calls += 1;
        await new Promise((resolve) => setTimeout(resolve, 10));
        return calls;
      });
    const [a, b] = await Promise.all([run(), run()]);
    expect(calls).toBe(1);
    expect(a).toBe(1);
    expect(b).toBe(1);
  });
});
