/* The gain stage.
 *
 * Present and inert in slice 1: every track's `gain_db` is null today, and null
 * means unity. Slice 2 measures EBU R128 loudness during the embedding pass it
 * is already decoding every file for, fills the column, and this starts doing
 * something without any of it being rebuilt.
 *
 * Two audio elements (gapless and crossfade) share one AudioContext, each with
 * its own gain node, track gain and crossfade factor.
 *
 * Two things make this delicate enough to keep in its own file.
 *
 * `createMediaElementSource` permanently reroutes an element's audio through
 * the Web Audio graph — there is no detaching it — and on iOS that interacts
 * badly with an AudioContext created before a user gesture. So each element's graph is
 * built on its first `play` event and never before, and every step is guarded:
 * if anything throws, the element keeps playing on its own and the listener
 * hears music rather than silence. An inert feature must never be able to
 * break the one thing this slice is for.
 */

let context: AudioContext | null = null;
const attempted = new Set<HTMLAudioElement>();
const nodes = new Map<HTMLAudioElement, GainNode>();
const dbs = new Map<HTMLAudioElement, number | null>();
const fades = new Map<HTMLAudioElement, number>();

const VOLUME_KEY = "muse.volume";

/** The listener's volume, 0 to 1, remembered across sessions. */
export function getVolume(): number {
  try {
    const stored = Number(localStorage.getItem(VOLUME_KEY));
    if (localStorage.getItem(VOLUME_KEY) !== null && stored >= 0 && stored <= 1) return stored;
  } catch {
    // storage blocked: full volume
  }
  return 1;
}

export function setVolume(v: number): void {
  try {
    localStorage.setItem(VOLUME_KEY, String(Math.min(Math.max(v, 0), 1)));
  } catch {
    // not remembered, still applied
  }
  attempted.forEach(applyGain);
}

/** Called on each element's first `play`, when a user gesture has definitely happened. */
export function attachGain(audio: HTMLAudioElement | null): void {
  if (!audio || attempted.has(audio)) return;
  attempted.add(audio); // one attempt per element, whatever happens

  try {
    if (!context) {
      const Ctor = window.AudioContext ?? (window as never as {
        webkitAudioContext: typeof AudioContext;
      }).webkitAudioContext;
      if (!Ctor) {
        applyGain(audio);
        return;
      }
      context = new Ctor();
    }
    const source = context.createMediaElementSource(audio);
    const node = context.createGain();
    source.connect(node);
    node.connect(context.destination);
    nodes.set(audio, node);
    applyGain(audio);
  } catch {
    // Fall back to the bare element. Unity gain is the correct behaviour in
    // slice 1 anyway, so nothing is lost until slice 2 has data.
    applyGain(audio); // volume still reaches the bare element
  }
}

/** Set the gain for the track about to play. Null means unity. */
export function setGain(audio: HTMLAudioElement, gainDb: number | null): void {
  dbs.set(audio, gainDb);
  applyGain(audio);
}

/** Crossfade level, 0 to 1, multiplied into the element's gain. */
export function setFade(audio: HTMLAudioElement, factor: number): void {
  fades.set(audio, factor);
  applyGain(audio);
}

function applyGain(audio: HTMLAudioElement): void {
  const fade = fades.get(audio) ?? 1;
  const node = nodes.get(audio);
  if (!node || !context) {
    audio.volume = Math.min(Math.max(getVolume() * fade, 0), 1);
    return;
  }
  // Decibels to amplitude, times the listener's volume. Null is unity, not zero.
  const db = dbs.get(audio) ?? null;
  const value = (db == null ? 1 : 10 ** (db / 20)) * getVolume() * fade;
  try {
    node.gain.setValueAtTime(value, context.currentTime);
  } catch {
    node.gain.value = value;
  }
}

/** iOS suspends the context when the page is backgrounded. */
export function resumeGainContext(): void {
  if (context?.state === "suspended") void context.resume();
}
