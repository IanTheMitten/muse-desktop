/* Pure pieces of the downloader lock, kept import-free so vitest runs them in node. */

export const isUnlockChord = (e: {
  ctrlKey: boolean;
  shiftKey: boolean;
  altKey: boolean;
  code: string;
  key?: string; // ignored: macOS Option rewrites it
}): boolean => e.ctrlKey && e.shiftKey && e.altKey && e.code === "KeyD";

/** Chart rows the listener owns; rank numbers ride on the rows, so they survive. */
export const ownedChart = <T extends { track: unknown }>(rows: T[]): T[] =>
  rows.filter((s) => s.track != null);
