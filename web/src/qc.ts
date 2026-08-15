/** The QC status palette from the design packet, and how a level reads. */

import type { QcLevel } from "./types";

export interface QcPalette {
  bg: string;
  border: string;
  dot: string;
  text: string;
  word: string;
}

export const QC_PALETTE: Record<QcLevel, QcPalette> = {
  pass: { bg: "#ebf9ed", border: "#b9ecc3", dot: "#238444", text: "#105b2b", word: "Pass" },
  warn: { bg: "#fff3db", border: "#ffdb97", dot: "#da9900", text: "#7c3e00", word: "Warn" },
  fail: { bg: "#ffe8e6", border: "#ffd6d2", dot: "#db2131", text: "#6f0008", word: "Fail" },
  na: { bg: "#f8f8f8", border: "#ededed", dot: "#dfdfdf", text: "#767676", word: "N/A" },
};

export const formatCount = (value: number): string => value.toLocaleString("en-US");
