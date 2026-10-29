/* The gain stage.
 *
 * Present and inert in slice 1: every track's `gain_db` is null today, and null
 * means unity. Slice 2 measures EBU R128 loudness during the embedding pass it
 * is already decoding every file for, fills the column, and this starts doing
 * something without any of it being rebuilt.
 *
 * Two things make this delicate enough to keep in its own file.
 *
 * `createMediaElementSource` permanently reroutes an element's audio through
 * the Web Audio graph — there is no detaching it — and on iOS that interacts
 * badly with an AudioContext created before a user gesture. So the graph is
 * built on the first `play` event and never before, and every step is guarded:
 * if anything throws, the element keeps playing on its own and the listener
 * hears music rather than silence. An inert feature must never be able to
 * break the one thing this slice is for.
 */

let context: AudioContext | null = null;
let gainNode: GainNode | null = null;
let attached = false;
let element: HTMLAudioElement | null = null;
let pendingGainDb: number | null = null;

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
  applyGain(pendingGainDb);
}

/** Called on the first `play`, when a user gesture has definitely happened. */
export function attachGain(audio: HTMLAudioElement | null): void {
  if (attached || !audio) return;
  attached = true; // one attempt, whatever happens
  element = audio;

  try {
    const Ctor = window.AudioContext ?? (window as never as {
      webkitAudioContext: typeof AudioContext;
    }).webkitAudioContext;
    if (!Ctor) {
      applyGain(null);
      return;
    }

    context = new Ctor();
    const source = context.createMediaElementSource(audio);
    gainNode = context.createGain();
    source.connect(gainNode);
    gainNode.connect(context.destination);
    applyGain(pendingGainDb);
  } catch {
    // Fall back to the bare element. Unity gain is the correct behaviour in
    // slice 1 anyway, so nothing is lost until slice 2 has data.
    context = null;
    gainNode = null;
    applyGain(null); // volume still reaches the bare element
  }
}

/** Set the gain for the track about to play. Null means unity. */
export function setGain(gainDb: number | null): void {
  pendingGainDb = gainDb;
  applyGain(gainDb);
}

function applyGain(gainDb: number | null): void {
  if (!gainNode || !context) {
    if (element) element.volume = getVolume();
    return;
  }
  // Decibels to amplitude, times the listener's volume. Null is unity, not zero.
  const value = (gainDb == null ? 1 : 10 ** (gainDb / 20)) * getVolume();
  try {
    gainNode.gain.setValueAtTime(value, context.currentTime);
  } catch {
    gainNode.gain.value = value;
  }
}

/** iOS suspends the context when the page is backgrounded. */
export function resumeGainContext(): void {
  if (context?.state === "suspended") void context.resume();
}
