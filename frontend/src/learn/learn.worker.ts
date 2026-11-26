/// <reference lib="webworker" />
import { runJob, type JobState, type LearnJob, type LearnRead } from "./job";

interface Request {
  id: number;
  job: LearnJob;
}

interface Reply {
  id: number;
  read: LearnRead;
}

const ctx: DedicatedWorkerGlobalScope = self as unknown as DedicatedWorkerGlobalScope;
let state: JobState | null = null;

ctx.onmessage = (ev: MessageEvent<Request>) => {
  const done = runJob(ev.data.job, state);
  state = done.state;
  ctx.postMessage({ id: ev.data.id, read: done.read } satisfies Reply);
};
