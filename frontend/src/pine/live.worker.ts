/// <reference lib="webworker" />
import { runPineScript, type RunRequest, type RunResult } from "./runEngine";

export type WorkerIn =
  | { type: "run"; request: RunRequest }
  | { type: "ping" };

export type WorkerOut =
  | { type: "result"; result: RunResult }
  | { type: "pong" }
  | { type: "error"; scriptId: string; message: string };

const ctx: DedicatedWorkerGlobalScope = self as unknown as DedicatedWorkerGlobalScope;

ctx.onmessage = (ev: MessageEvent<WorkerIn>) => {
  const msg = ev.data;
  if (msg.type === "ping") {
    ctx.postMessage({ type: "pong" } satisfies WorkerOut);
    return;
  }
  if (msg.type === "run") {
    void runPineScript(msg.request)
      .then((result) => {
        ctx.postMessage({ type: "result", result } satisfies WorkerOut);
      })
      .catch((err: unknown) => {
        ctx.postMessage({
          type: "error",
          scriptId: msg.request.scriptId,
          message: err instanceof Error ? err.message : String(err),
        } satisfies WorkerOut);
      });
  }
};
