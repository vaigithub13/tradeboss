/** Paper alert sounds, made with the Web Audio API (no audio files). Browsers start audio only after a click, so
 * `unlockAudio` must run from one (the panel's "Enable sounds"); until then nothing plays. */

export interface Tone {
  /** seconds from the sound's start */
  at: number;
  freq: number;
  /** seconds until the tone has died away */
  dur: number;
  /** peak, 0..1 before the volume */
  gain: number;
  type: OscillatorType;
}

const CHIME = [523.25, 659.25, 783.99, 1046.5]; // C5 E5 G5 C6, rising
const CHIME_STEP = 0.18;
const CHIME_RING = 1.45;
export const ENTRY_LENGTH = CHIME_STEP * (CHIME.length - 1) + CHIME_RING; // about 2 s
const ENTRY_GAP = 0.15;

/** ENTRY: a loud rising chime (about 2 s), played twice. Each note has a bright octave above it. */
export function entryPlan(): Tone[] {
  const once = CHIME.flatMap((freq, i) => [
    { at: i * CHIME_STEP, freq, dur: CHIME_RING, gain: 0.9, type: "triangle" as const },
    { at: i * CHIME_STEP, freq: freq * 2, dur: CHIME_RING * 0.6, gain: 0.35, type: "sine" as const },
  ]);
  return [...once, ...once.map((t) => ({ ...t, at: t.at + ENTRY_LENGTH + ENTRY_GAP }))];
}

// a bell's partials are not harmonic: that is what makes it sound like metal, not a note
const BELL_PARTIALS: [ratio: number, gain: number][] = [[1, 0.8], [2.0, 0.4], [2.76, 0.35], [5.4, 0.18], [8.93, 0.08]];
const BELL_STRIKES = [196, 196, 147]; // G3, G3, then down to D3
const BELL_STEP = 0.55;
const BELL_RING = 1.8;

/** EXIT: a low triple bell, the last strike lower. Nothing like the entry chime. */
export function exitPlan(): Tone[] {
  return BELL_STRIKES.flatMap((base, i) =>
    BELL_PARTIALS.map(([ratio, gain]) => ({
      at: i * BELL_STEP, freq: base * ratio, dur: BELL_RING / Math.sqrt(ratio), gain, type: "sine" as const,
    })),
  );
}

export function planLength(plan: Tone[]): number {
  return plan.reduce((end, t) => Math.max(end, t.at + t.dur), 0);
}

let ctx: AudioContext | null = null;
let master: GainNode | null = null;
let nextFree = 0;

/** From a click: create (or resume) the audio context. True when sound can play. */
export async function unlockAudio(): Promise<boolean> {
  try {
    if (ctx === null) {
      const Ctor = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (!Ctor) return false;
      ctx = new Ctor();
      const limiter = ctx.createDynamicsCompressor(); // loud without clipping when tones stack
      master = ctx.createGain();
      master.connect(limiter);
      limiter.connect(ctx.destination);
    }
    if (ctx.state === "suspended") await ctx.resume();
    return ctx.state === "running";
  } catch {
    return false;
  }
}

export function audioReady(): boolean {
  return ctx !== null && ctx.state === "running";
}

/** Play a plan at `volume` (0..1). Sounds queue one after another, so two alerts in one poll are both heard. */
export function playPlan(plan: Tone[], volume: number): void {
  if (ctx === null || master === null || ctx.state !== "running") return;
  const start = Math.max(ctx.currentTime + 0.02, nextFree);
  master.gain.setValueAtTime(Math.max(0, Math.min(1, volume)), start);
  for (const tone of plan) {
    const osc = ctx.createOscillator();
    const env = ctx.createGain();
    const t0 = start + tone.at;
    osc.type = tone.type;
    osc.frequency.setValueAtTime(tone.freq, t0);
    env.gain.setValueAtTime(0.0001, t0);
    env.gain.exponentialRampToValueAtTime(tone.gain, t0 + 0.01);
    env.gain.exponentialRampToValueAtTime(0.0001, t0 + tone.dur);
    osc.connect(env);
    env.connect(master);
    osc.start(t0);
    osc.stop(t0 + tone.dur + 0.05);
  }
  nextFree = start + planLength(plan) + 0.1;
}

export function playAlertSound(kind: "entry" | "exit", volume: number): void {
  playPlan(kind === "entry" ? entryPlan() : exitPlan(), volume);
}
