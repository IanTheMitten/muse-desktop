import type { RepeatMode } from "./sync";

/* Pure queue edits. They act only on slots after the playhead, so `index`
 * never needs fixing up; anything else returns the input array unchanged. */

export function insertNext<T>(queue: T[], index: number, tracks: T[]): T[] {
  return [...queue.slice(0, index + 1), ...tracks, ...queue.slice(index + 1)];
}

export function append<T>(queue: T[], tracks: T[]): T[] {
  return [...queue, ...tracks];
}

const editable = (length: number, index: number, at: number) =>
  Number.isInteger(at) && at > index && at < length;

export function removeAt<T>(queue: T[], index: number, at: number): T[] {
  if (!editable(queue.length, index, at)) return queue;
  return [...queue.slice(0, at), ...queue.slice(at + 1)];
}

export function move<T>(queue: T[], index: number, from: number, to: number): T[] {
  if (from === to || !editable(queue.length, index, from) || !editable(queue.length, index, to)) {
    return queue;
  }
  const out = [...queue];
  const [item] = out.splice(from, 1);
  out.splice(to, 0, item);
  return out;
}

type Mode = { shuffle: boolean; repeat: RepeatMode };

export function nextIndex(
  length: number,
  at: number,
  { shuffle, repeat }: Mode,
  random: () => number = Math.random,
): number {
  if (length === 0) return at;
  if (shuffle) {
    if (length === 1) return at;
    let pick = at;
    while (pick === at) pick = Math.floor(random() * length);
    return pick;
  }
  if (at + 1 < length) return at + 1;
  return repeat === "all" ? 0 : at;
}

/** The slot that will certainly play after `at`, or null when it is not knowable. */
export function upcomingIndex(length: number, at: number, { shuffle, repeat }: Mode): number | null {
  if (shuffle || repeat === "one") return null;
  if (at + 1 < length) return at + 1;
  return repeat === "all" && at !== 0 ? 0 : null;
}
