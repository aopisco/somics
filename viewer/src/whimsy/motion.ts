/** Pure, deterministic motion curves. Every animated part of the scene pulls from here
 *  so the whole thing breathes at one tempo instead of each piece inventing its own.
 *
 *  Every function takes seconds and returns a plain number, so they are trivially
 *  testable and callable from inside useFrame without allocating.
 */

const TAU = Math.PI * 2;

/** Slow in-and-out swell around 1, for idle scale. */
export function breathe(t: number, rate = 0.22, depth = 0.014): number {
  return 1 + Math.sin(t * TAU * rate) * depth;
}

/** Two detuned sines, so an idle rotation never looks like a perfect turntable. */
export function wobble(t: number, amplitude = 0.045): number {
  return (Math.sin(t * 0.37) * 0.7 + Math.sin(t * 0.91 + 1.1) * 0.3) * amplitude;
}

/**
 * Mostly zero, with an occasional sharp flick — a nose twitch or an ear flick.
 *
 * `period` is the seconds between flicks and `duty` the fraction of that spent
 * flicking, so the default is a 90 ms twitch roughly every 1.7 s.
 */
export function twitch(t: number, period = 1.7, duty = 0.055, phase = 0): number {
  const cycle = ((t + phase) % period) / period;
  if (cycle > duty) return 0;
  return Math.sin((cycle / duty) * Math.PI);
}

/** Wind: a travelling wave, so neighbouring grass blades lean in sequence. */
export function sway(t: number, offset: number, rate = 1.1, amplitude = 1): number {
  return (Math.sin(t * rate + offset) * 0.75 + Math.sin(t * rate * 2.3 + offset * 1.7) * 0.25) * amplitude;
}

/** Overshoot-and-settle, for pins popping in. Reaches 1 at t=1 and stays there. */
export function springPop(t: number, overshoot = 1.7): number {
  if (t <= 0) return 0;
  if (t >= 1) return 1;
  const decay = Math.exp(-6 * t);
  return 1 - decay * Math.cos(t * Math.PI * overshoot);
}

export function easeInOutCubic(t: number): number {
  const clamped = Math.min(1, Math.max(0, t));
  return clamped < 0.5 ? 4 * clamped ** 3 : 1 - (-2 * clamped + 2) ** 3 / 2;
}

export const lerp = (a: number, b: number, t: number): number => a + (b - a) * t;

/** Frame-rate independent approach toward a target; `speed` is per second. */
export function damp(current: number, target: number, speed: number, delta: number): number {
  return lerp(current, target, 1 - Math.exp(-speed * delta));
}

/**
 * Deterministic value in [0, 1) from an integer seed.
 *
 * Scene decoration needs scatter that survives a reload and a shared URL, so grass and
 * motes index into this rather than calling Math.random.
 */
export function hashNoise(seed: number): number {
  const x = Math.sin(seed * 127.1 + 311.7) * 43758.5453;
  return x - Math.floor(x);
}
