/** Loading copy. Deterministic from a seed so a shared link shows the same line. */

import { hashNoise } from "./motion";

export const LOADING_LINES = [
  "waking the rat",
  "counting whiskers",
  "asking the colon politely",
  "unrolling the section",
  "warming up 587,115 cells",
  "negotiating with Cloudflare",
  "brushing the grass",
  "aligning micron to pixel",
  "nudging the camera",
  "reticulating villi",
] as const;

export function loadingLine(seed: number): string {
  const index = Math.floor(hashNoise(seed + 1) * LOADING_LINES.length);
  return LOADING_LINES[Math.min(index, LOADING_LINES.length - 1)]!;
}
