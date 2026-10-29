import { withToken } from "../token";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import {
  getRecommendations,
  rateTrack,
  RELAXATION_COPY,
  statusOf,
  type Rating,
  type Reasons,
  type Track,
} from "../api";
import { attachGain, resumeGainContext, setGain } from "./gain";
import { append, insertNext, move, nextIndex, removeAt } from "./queue";
import {
  PAUSE_TIMEOUT_MS,
  beatSpan,
  closeSpan,
  openSpan,
  type EndedReason,
} from "./plays";
import {
  HEARTBEAT_MS,
  readState,
  writeState,
  type RepeatMode,
} from "./sync";

/* The audio element lives here, above the router, and is rendered exactly
 * once. This is structural, not an optimisation: an <audio> element inside a
 * route unmounts the moment the listener taps an artist, and the music stops.
 *
 * The queue lives on the server (ticket 04), so closing the laptop mid-song
 * and opening the phone resumes the same queue at the same position. Audio
 * always plays locally on whichever device is open — no remote control, no
 * takeover. */

type PlayerApi = {
  queue: Track[];
  index: number;
  current: Track | null;
  playing: boolean;
  positionMs: number;
  durationMs: number;
  shuffle: boolean;
  repeat: RepeatMode;
  restored: boolean;
  playQueue: (tracks: Track[], startIndex: number, from?: PlayingFrom) => void;
  enqueue: (tracks: Track[]) => void;
  playNext: (tracks: Track[]) => void;
  moveInQueue: (from: number, to: number) => void;
  removeFromQueue: (at: number) => void;
  clearQueue: () => void;
  toggle: () => void;
  next: () => void;
  previous: () => void;
  seek: (ms: number) => void;
  toggleShuffle: () => void;
  cycleRepeat: () => void;
  /** Tapping the thumb the track already carries clears it; the other thumb
   *  flips it. */
  rate: (rating: Exclude<Rating, null>) => void;

  /* --- radio (story 35) ------------------------------------------------ */
  /** On when the queue is being kept topped up by the recommender. */
  radio: boolean;
  /** A fetch is in flight. Shown, not hidden: over the tailnet the append is
   *  usually invisible, and on a cold CLAP load it is not. */
  radioBusy: boolean;
  /** Why the last append was worse than it should have been, in the
   *  listener's words. Null when radio is behaving. `failed` separates radio
   *  not working from radio working with less to work with, so the screen can
   *  tell the two apart without matching on the copy — which would break the
   *  moment a sentence is reworded. */
  radioNote: RadioNote | null;
  /** Why radio picked a track (story 37). Null for anything the listener
   *  queued themselves. */
  reasonsFor: (trackRowid: number) => Reasons | null;
  /** The listener's mood target; Muse picks are re-ranked toward it. */
  mood: MoodPoint | null;
  setMood: (p: MoodPoint | null) => void;
  /** Clears a stall, and retries a failed mood re-pick. */
  retryMuse: () => void;
  playingFrom: PlayingFrom | null;
  /** True for tracks Muse added, false for anything the listener queued. */
  isMusePick: (rowid: number) => boolean;
};

export type MoodPoint = { v: number; a: number };

export type PlayingFrom = {
  kind: "album" | "playlist" | "artist" | "track" | "home" | "muse";
  label: string;
  playlistId?: number;
};

export type RadioNote = { text: string; failed: boolean };

/** How many unplayed tracks radio keeps ahead of the listener. Eight, so the
 *  "up next" list is long enough to read as a queue rather than a guess, and
 *  bounded so radio stops asking once it is full instead of extending forever.
 *  Each pick is still fetched one at a time from the queue's tail, so the
 *  eighth is derived from the seventh rather than all eight from one anchor —
 *  filling a short queue costs one request per missing track, which is what
 *  starting a fresh selection with radio on pays for.
 *
 *  This is an invariant on the queue, not only a brake on fetching. A brake
 *  alone held the steady state and nothing else: anything that raced the
 *  listener through the queue — a burst of selections, a device advancing on
 *  its own — dropped the gap under eight over and over, and each drop bought
 *  one more append. One session reached sixty tracks with the listener at the
 *  thirteenth, so forty-seven picks sat there anchored an hour behind what was
 *  actually playing. Radio that learns from what you just heard cannot serve a
 *  plan made forty-seven tracks ago, so the tail is trimmed back to the limit
 *  rather than left standing. */
const RADIO_LOOKAHEAD = 8;

/** The rolling exclusion set is capped so the query string stays sane on a
 *  session that runs for hours. Dropping the oldest entries first is the same
 *  thing the server's relaxation ladder does when it runs dry. Also the cap on
 *  `seen`, which is where the set is now sourced from. */
const RADIO_EXCLUDE_CAP = 300;

/** How many played tracks artist spacing is measured against. The server only
 *  reads the first few; sending more is free and keeps the client from having
 *  to know the server's spacing constant. */
const RADIO_RECENT = 10;

const MOOD_KEY = "muse.mood";

function storedMood(): MoodPoint | null {
  try {
    const raw = localStorage.getItem(MOOD_KEY);
    return raw ? (JSON.parse(raw) as MoodPoint) : null;
  } catch {
    // A corrupt preference is not worth a broken player.
    return null;
  }
}

const PlayerContext = createContext<PlayerApi | null>(null);

export function usePlayer(): PlayerApi {
  const api = useContext(PlayerContext);
  if (!api) throw new Error("usePlayer used outside PlayerProvider");
  return api;
}

export function PlayerProvider({ children }: { children: ReactNode }) {
  const audio = useRef<HTMLAudioElement | null>(null);

  const [queue, setQueue] = useState<Track[]>([]);
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [positionMs, setPositionMs] = useState(0);
  const [durationMs, setDurationMs] = useState(0);
  const [shuffle, setShuffle] = useState(false);
  const [repeat, setRepeat] = useState<RepeatMode>("off");
  const [restored, setRestored] = useState(false);

  /* Radio is one nullable number: the track the session started from. Null is
   * off. It lives in `player_state` beside shuffle and repeat so it follows
   * the listener between devices, and the seed is kept rather than a bare flag
   * because "what did this session grow out of" is the one thing a listener
   * asks about a radio session an hour in. */
  const [radioSeed, setRadioSeed] = useState<number | null>(null);
  const [radioBusy, setRadioBusy] = useState(false);
  const [radioNote, setRadioNote] = useState<RadioNote | null>(null);
  const [mood, setMoodState] = useState<MoodPoint | null>(storedMood);
  const [playingFrom, setPlayingFrom] = useState<PlayingFrom | null>(null);

  /* Why each radio pick was made, kept out of `queue` on purpose: the queue is
   * tracks, and a track carries no opinion about why it is where it is. */
  const [reasons, setReasons] = useState<Map<number, Reasons>>(() => new Map());

  /* Which rowids radio put in the queue, so their play spans are recorded with
   * `source = 'radio'` and the history can later be read back per source. */
  const radioAdded = useRef<Set<number>>(new Set());
  /* Every rowid that has been in the queue this session, oldest first. The
   * exclusion set is read from here rather than from `queue`, because the
   * queue is trimmed: a trimmed track is gone from the array while still being
   * something the listener has just been offered, and sourcing exclusions from
   * the array would hand that track straight back as the next pick and churn
   * on it. Bounded by the same cap the query string is. */
  const seen = useRef<number[]>([]);
  /* One append in flight at a time. */
  const filling = useRef(false);
  /* The anchor an append last failed to extend. A seed with no vectors 404s
   * and a dry library returns nothing, and in both cases the queue does not
   * grow -- so without this the effect would re-fire on its own output
   * forever. It is a record of no-progress, not of having tried: a successful
   * append clears it, and so does anything the listener does, because a
   * listener acting is the signal that a retry is worth one more request. */
  const stalledAt = useRef<number | null>(null);
  const lastIndex = useRef(0);
  /* Live copies for the debounced mood re-rank, which must not re-key on the
   * queue. */
  const moodRef = useRef(mood);
  moodRef.current = mood;
  const queueRef = useRef(queue);
  queueRef.current = queue;
  const indexRef = useRef(index);
  indexRef.current = index;
  const radioSeedRef = useRef(radioSeed);
  radioSeedRef.current = radioSeed;
  const moodFailed = useRef(false);
  const firstMood = useRef(true);

  /* Where a restored session should resume from. Applied once, when the
   * element reports it has metadata — setting currentTime before that is
   * silently ignored by every browser. */
  const resumeAt = useRef<number | null>(null);
  /* True only for tracks the listener actually selected. A restored queue must
   * not start playing on its own: opening the app should not make noise. */
  const autoplay = useRef(false);

  /* The open play span, and why the next close will happen. The reason is set
   * by whatever the listener did — pressing next, letting the track end — and
   * read when the span actually closes, so the record says what happened
   * rather than what could be inferred afterwards. */
  const playId = useRef<number | null>(null);
  const closingReason = useRef<EndedReason>("new_selection");
  const seekCount = useRef(0);
  const positionRef = useRef(0);
  const pauseTimer = useRef<number | null>(null);

  const current = queue[index] ?? null;

  /** Record rowids as offered. Re-offering something moves it to the young end
   *  rather than duplicating it, so the cap drops what was heard longest ago
   *  instead of whatever happens to have been written most often. */
  const remember = useCallback((rowids: number[]) => {
    if (rowids.length === 0) return;
    const fresh = new Set(rowids);
    seen.current = [
      ...seen.current.filter((id) => !fresh.has(id)),
      ...rowids,
    ].slice(-RADIO_EXCLUDE_CAP);
  }, []);

  // --- restore from the server, once, on open ---------------------------
  useEffect(() => {
    const abort = new AbortController();
    readState(abort.signal)
      .then((state) => {
        if (abort.signal.aborted) return;
        setQueue(state.queue);
        remember(state.queue.map((track) => track.track_rowid));
        setIndex(Math.min(state.queue_index, Math.max(state.queue.length - 1, 0)));
        setPositionMs(state.position_ms);
        setShuffle(Boolean(state.shuffle));
        setRepeat(state.repeat_mode);
        setRadioSeed(state.radio_seed);
        radioAdded.current = new Set(state.radio_added ?? []);
        resumeAt.current = state.position_ms;
      })
      .catch(() => {
        /* A player that cannot reach its state still plays. */
      })
      .finally(() => setRestored(true));
    return () => abort.abort();
  }, []);

  // --- queue control ----------------------------------------------------

  /** Close whatever is open, with the reason the listener's action implies. */
  const endSpan = useCallback((reason: EndedReason) => {
    const id = playId.current;
    if (id == null) return;
    playId.current = null;
    void closeSpan(id, reason, positionRef.current);
  }, []);

  const playQueue = useCallback(
    (tracks: Track[], startIndex: number, from?: PlayingFrom) => {
      closingReason.current = "new_selection";
      autoplay.current = true;
      resumeAt.current = null;
      // A new selection starts with no Muse picks; a jump within the live queue keeps them.
      if (tracks !== queueRef.current) { radioAdded.current = new Set(); setReasons(new Map()); }
      setQueue(tracks);
      remember(tracks.map((track) => track.track_rowid));
      setIndex(startIndex);
      setPositionMs(0);
      setPlayingFrom(from ?? null);
      // Everything but a playlist is followed by Muse.
      setRadioSeed(
        from?.kind === "playlist" ? null : tracks[startIndex]?.track_rowid ?? null,
      );
      stalledAt.current = null;
    },
    [remember],
  );

  const enqueue = useCallback(
    (tracks: Track[]) => {
      if (queueRef.current.length === 0) {
        playQueue(tracks, 0, { kind: "track", label: "Queue" });
        return;
      }
      setQueue((q) => append(q, tracks));
      remember(tracks.map((track) => track.track_rowid));
    },
    [remember, playQueue],
  );

  const playNext = useCallback(
    (tracks: Track[]) => {
      if (queueRef.current.length === 0) {
        playQueue(tracks, 0, { kind: "track", label: "Queue" });
        return;
      }
      setQueue((q) => insertNext(q, indexRef.current, tracks));
      remember(tracks.map((track) => track.track_rowid));
    },
    [remember, playQueue],
  );

  const removeFromQueue = useCallback((at: number) => {
    setQueue((q) => removeAt(q, indexRef.current, at));
  }, []);

  const moveInQueue = useCallback((from: number, to: number) => {
    setQueue((q) => move(q, indexRef.current, from, to));
  }, []);

  const clearQueue = useCallback(() => {
    autoplay.current = false;
    audio.current?.pause();
    setQueue([]);
    setIndex(0);
    setPositionMs(0);
    // Radio anchors on the queue's tail. With no queue there is nothing to
    // extend, so it stops rather than sitting on as a mode that cannot act.
    setRadioSeed(null);
    setRadioNote(null);
    setPlayingFrom(null);
  }, []);

  const next = useCallback(() => {
    autoplay.current = true;
    // Only overwrite when the listener drove it; `onEnded` sets `track_end`
    // just before calling this.
    if (closingReason.current !== "track_end") {
      closingReason.current = "user_next";
    }
    setIndex((at) => nextIndex(queue.length, at, { shuffle, repeat }));
  }, [queue.length, shuffle, repeat]);

  const previous = useCallback(() => {
    const element = audio.current;
    // Standard player behaviour: previous restarts the track unless you are
    // already near its start.
    if (element && element.currentTime > 3) {
      element.currentTime = 0;
      return;
    }
    autoplay.current = true;
    closingReason.current = "user_prev";
    setIndex((at) => (at > 0 ? at - 1 : at));
  }, []);

  const toggle = useCallback(() => {
    const element = audio.current;
    if (!element || !current) return;
    resumeGainContext();
    if (element.paused) {
      autoplay.current = true;
      void element.play();
    } else {
      element.pause();
    }
  }, [current]);

  const seek = useCallback((ms: number) => {
    const element = audio.current;
    if (!element) return;
    element.currentTime = ms / 1000;
    setPositionMs(ms);
    // How much the listener fidgeted is physical fact and part of the record.
    seekCount.current += 1;
  }, []);

  /* Applied to the queue first and sent afterwards. A thumb that waits for the
   * tailnet before it moves feels broken; if the write fails the queue goes
   * back to what the server still believes, rather than showing an opinion
   * that was never recorded. */
  const rate = useCallback(
    (rating: Exclude<Rating, null>) => {
      const track = queue[index];
      if (!track) return;
      const previous = track.rating;
      const next: Rating = previous === rating ? null : rating;

      const apply = (value: Rating) =>
        setQueue((tracks) =>
          tracks.map((entry, at) =>
            at === index ? { ...entry, rating: value } : entry,
          ),
        );

      apply(next);
      rateTrack(track.track_rowid, next).catch(() => apply(previous));
    },
    [queue, index],
  );

  const toggleShuffle = useCallback(() => setShuffle((on) => !on), []);

  const cycleRepeat = useCallback(
    () =>
      setRepeat((mode) =>
        mode === "off" ? "all" : mode === "all" ? "one" : "off",
      ),
    [],
  );

  // --- radio (story 35) -------------------------------------------------
  //
  // There is no radio endpoint and no server-side session. The client already
  // owns the queue, so radio is repeated calls to `/api/recommend/` with the
  // moving query point as the path parameter and the rolling exclusion set as
  // `exclude` -- which is why the server can stay stateless and why a second
  // source of truth about what is playing never comes into existence.

  /* The query point is the queue's tail, not what is playing. Each pick is
   * derived from the pick before it, so an hour of radio migrates away from
   * the seed instead of orbiting it (story 36) -- and the listener hears the
   * migration in the order they play, which is the order it was built in. */
  const anchor = queue.length > 0 ? queue[queue.length - 1] : null;
  const ahead = queue.length - index - 1;

  useEffect(() => {
    if (radioSeed == null || anchor == null) return;
    if (ahead >= RADIO_LOOKAHEAD) return;
    if (filling.current) return;

    /* Moving through the queue clears a stall. The listener pressing next is
     * the one signal that something might have changed -- the network came
     * back, or the exclusion set moved far enough that the ladder can find
     * something -- and one request per press is worth paying to have radio
     * recover on its own instead of staying dead until it is toggled off and
     * on. Cleared here rather than in an effect of its own, which would run
     * after this one and so arrive a render too late to help the press that
     * caused it. */
    if (lastIndex.current !== index) {
      lastIndex.current = index;
      stalledAt.current = null;
    }
    if (stalledAt.current === anchor.track_rowid) return;

    filling.current = true;
    setRadioBusy(true);

    const exclude = seen.current.slice(-RADIO_EXCLUDE_CAP);
    // Most recent first: the server measures artist spacing against the head
    // of this list.
    const recent = queue
      .slice(Math.max(0, index + 1 - RADIO_RECENT), index + 1)
      .map((track) => track.track_rowid)
      .reverse();

    getRecommendations(anchor.track_rowid, {
      n: 1,
      exclude,
      recent,
      ...(moodRef.current ? { mood: moodRef.current } : {}),
    })
      .then((response) => {
        const pick = response.results[0];
        if (!pick) {
          stalledAt.current = anchor.track_rowid;
          setRadioNote({
            text: "Nothing left to play that you have not just heard.",
            failed: false,
          });
          return;
        }
        if (queue.some((track) => track.track_rowid === pick.track_rowid)) {
          // The queue moved while this was in flight and already holds the
          // pick. Appending it would double a track; not marking the anchor
          // stalled would re-ask the same question and get the same answer.
          stalledAt.current = anchor.track_rowid;
          return;
        }
        stalledAt.current = null;
        radioAdded.current.add(pick.track_rowid);
        remember([pick.track_rowid]);
        setReasons((existing) => {
          const next = new Map(existing);
          next.set(pick.track_rowid, pick.reasons);
          return next;
        });
        setQueue((tracks) =>
          tracks.some((t) => t.track_rowid === pick.track_rowid)
            ? tracks
            : [...tracks, pick.track],
        );
        // A relaxed constraint is shown rather than swallowed: the listener
        // should be able to tell "the library is running dry" from "the
        // recommender got worse".
        setRadioNote(
          pick.reasons.relaxed.length > 0
            ? {
                text: `Running low — ${pick.reasons.relaxed
                  .map((rung) => RELAXATION_COPY[rung])
                  .join(", ")}.`,
                failed: false,
              }
            : null,
        );
      })
      .catch((cause: unknown) => {
        stalledAt.current = anchor.track_rowid;
        setRadioNote({
          // Was a substring test for "404" against the message, which the
          // path is also part of: a seed whose rowid happens to be 404 would
          // have read any failure as a missing embedding.
          text:
            statusOf(cause) === 404
              ? "This track has no embedding yet, so radio cannot follow it."
              : "Could not reach the recommender.",
          failed: true,
        });
      })
      .finally(() => {
        filling.current = false;
        setRadioBusy(false);
      });
  }, [radioSeed, anchor, ahead, queue, index, remember]);

  /* The other half of the limit. The append above refuses to grow the queue
   * past eight ahead; this cuts it back when something else already has. The
   * two together are what makes eight a number the listener can rely on rather
   * than one that holds only while nothing unusual happens.
   *
   * Only while radio is on, and only the tail: what is behind the listener is
   * theirs to walk back through, and a queue with radio off is exactly what
   * they put in it. Trimmed rowids stay in `seen`, so cutting a track is not
   * an invitation to pick it again on the next request.
   *
   * Off under shuffle and repeat-all, which are the two modes where the index
   * moves backwards. Under either, "eight after the index" is not a lookahead
   * at all -- a shuffle landing on the third of twenty-two would read as
   * fourteen too many and delete them, and repeat-all wrapping to the top
   * would delete the whole queue behind the wrap. Refusing to fetch under
   * those modes is only radio idling; refusing to trim is the difference
   * between idling and destroying what is queued. */
  useEffect(() => {
    if (radioSeed == null || shuffle || repeat === "all") {
      return;
    }
    const limit = index + 1 + RADIO_LOOKAHEAD;
    if (queue.length <= limit) return;
    // Only Muse's picks past the limit go; a listener's own tracks stay.
    setQueue((tracks) =>
      tracks.length > limit
        ? tracks.filter((t, i) => i < limit || !radioAdded.current.has(t.track_rowid))
        : tracks,
    );
  }, [radioSeed, index, queue.length, shuffle, repeat]);

  const reasonsFor = useCallback(
    (trackRowid: number) => reasons.get(trackRowid) ?? null,
    [reasons],
  );

  const isMusePick = useCallback(
    (rowid: number) => radioAdded.current.has(rowid),
    [],
  );

  /* A mood change re-ranks only Muse's picks after the playhead. Reads the
   * live queue through refs so the debounce is keyed on the mood alone. */
  const rerank = useCallback(() => {
    const tracks = queueRef.current;
    const at = indexRef.current;
    if (radioSeedRef.current == null || !tracks[at]) return;
    const added = radioAdded.current;
    const stale = new Set(
      tracks
        .slice(at + 1)
        .filter((t) => added.has(t.track_rowid))
        .map((t) => t.track_rowid),
    );
    if (stale.size === 0) return;
    const anchorTrack =
      [...tracks.slice(at)].reverse().find((t) => !added.has(t.track_rowid)) ??
      tracks[at];
    // The picks being replaced are fair game again.
    const exclude = seen.current
      .filter((id) => !stale.has(id))
      .slice(-RADIO_EXCLUDE_CAP);
    const recent = tracks
      .slice(Math.max(0, at + 1 - RADIO_RECENT), at + 1)
      .map((t) => t.track_rowid)
      .reverse();
    const fail = () => {
      moodFailed.current = true;
      setRadioNote({ text: "Could not re-pick for this mood.", failed: true });
    };
    const target = moodRef.current;
    getRecommendations(anchorTrack.track_rowid, {
      n: RADIO_LOOKAHEAD,
      exclude,
      recent,
      ...(target ? { mood: target } : {}),
    })
      .then((response) => {
        if (response.results.length === 0) return fail();
        moodFailed.current = false;
        setRadioNote(null);
        stale.forEach((id) => added.delete(id));
        response.results.forEach((r) => added.add(r.track_rowid));
        remember(response.results.map((r) => r.track_rowid));
        setReasons((existing) => {
          const next = new Map(existing);
          response.results.forEach((r) => next.set(r.track_rowid, r.reasons));
          return next;
        });
        setQueue((existing) => {
          const cut = indexRef.current + 1;
          return [
            ...existing.slice(0, cut),
            ...existing.slice(cut).filter((t) => !stale.has(t.track_rowid)),
            ...response.results.map((r) => r.track),
          ];
        });
      })
      .catch(fail);
  }, [remember]);

  const setMood = useCallback((p: MoodPoint | null) => setMoodState(p), []);

  useEffect(() => {
    try {
      if (mood) localStorage.setItem(MOOD_KEY, JSON.stringify(mood));
      else localStorage.removeItem(MOOD_KEY);
    } catch {
      /* private mode: the mood still works this session. */
    }
    if (firstMood.current) {
      firstMood.current = false;
      return;
    }
    const timer = window.setTimeout(rerank, 400);
    return () => window.clearTimeout(timer);
  }, [mood, rerank]);

  const retryMuse = useCallback(() => {
    stalledAt.current = null;
    if (moodFailed.current) rerank();
  }, [rerank]);

  // --- load the selected track ------------------------------------------
  useEffect(() => {
    const element = audio.current;
    if (!element || !current) return;

    element.src = withToken(`/api/tracks/${current.track_rowid}/stream`);
    element.load();
    setGain(current.gain_db);

    if (autoplay.current) {
      void element.play().catch(() => setPlaying(false));
    }

    // Closing happens on the way out, with the reason the listener's action
    // set. Anything the client cannot classify is left for the sweeper to
    // close as `lost`, which is the honest record of a client that stopped
    // reporting.
    return () => {
      endSpan(closingReason.current);
      closingReason.current = "new_selection";
      seekCount.current = 0;
      positionRef.current = 0;
    };
  }, [current, endSpan]);

  // A span opens when playback actually begins, not when a track is loaded:
  // a restored queue sitting paused has not been listened to.
  const beginSpan = useCallback(() => {
    if (playId.current != null || !current) return;
    const element = audio.current;
    const durationSeconds = element?.duration;
    const trackDuration = Number.isFinite(durationSeconds)
      ? (durationSeconds as number) * 1000
      : current.duration_ms ?? 0;

    const source = radioAdded.current.has(current.track_rowid)
      ? "radio"
      : "queue";

    void openSpan(current.track_rowid, trackDuration, source).then((id) => {
      // Guard against a track change that happened while this was in flight.
      if (id != null && audio.current && current === queue[index]) {
        playId.current = id;
      } else if (id != null) {
        void closeSpan(id, "new_selection", 0);
      }
    });
  }, [current, queue, index]);

  // --- push state to the server -----------------------------------------
  //
  // One request per ten seconds, and it carries position, queue, and mode
  // together. Only a playing client writes: an idle background tab that wrote
  // would silently drag the playing device's position backwards.
  const push = useCallback(
    (clientState: "playing" | "paused" | "idle") => {
      if (queue.length === 0) return;
      void writeState({
        queue: queue.map((track) => track.track_rowid),
        queue_index: index,
        position_ms: Math.round(positionMs),
        client_state: clientState,
        shuffle,
        repeat_mode: repeat,
        radio_seed: radioSeed,
        // Only what is still queued: the set is otherwise append-only for the
        // life of the tab, and the rest can no longer be played from here.
        radio_added: queue
          .map((track) => track.track_rowid)
          .filter((rowid) => radioAdded.current.has(rowid)),
      });
    },
    [queue, index, positionMs, shuffle, repeat, radioSeed],
  );

  const pushRef = useRef(push);
  pushRef.current = push;

  // One beat, two jobs: the queue position for continuity, and the span
  // position for the history. Same ten seconds, no extra traffic.
  useEffect(() => {
    if (!playing) return;
    const timer = setInterval(() => {
      pushRef.current("playing");
      if (playId.current != null) {
        void beatSpan(playId.current, positionRef.current, seekCount.current);
      }
    }, HEARTBEAT_MS);
    return () => clearInterval(timer);
  }, [playing]);

  // A pause that is never resumed is not a pause. Waiting it out means
  // stepping away from the desk is not recorded as abandoning the track.
  useEffect(() => {
    if (playing) {
      if (pauseTimer.current != null) {
        clearTimeout(pauseTimer.current);
        pauseTimer.current = null;
      }
      return;
    }
    if (playId.current == null) return;

    pauseTimer.current = window.setTimeout(() => {
      endSpan("pause_timeout");
      pauseTimer.current = null;
    }, PAUSE_TIMEOUT_MS);

    return () => {
      if (pauseTimer.current != null) clearTimeout(pauseTimer.current);
      pauseTimer.current = null;
    };
  }, [playing, endSpan]);

  // Selecting a track, or changing mode, should not wait up to ten seconds to
  // reach the other device.
  useEffect(() => {
    if (playing) pushRef.current("playing");
  }, [index, shuffle, repeat, radioSeed, playing]);

  // Leaving the page: one last position, so reopening elsewhere resumes where
  // the listener actually was. `keepalive` lets it outlive the tab.
  useEffect(() => {
    const flush = () => {
      if (playing) pushRef.current("playing");
    };
    window.addEventListener("pagehide", flush);
    return () => window.removeEventListener("pagehide", flush);
  }, [playing]);

  // --- Media Session ----------------------------------------------------
  useEffect(() => {
    if (!("mediaSession" in navigator) || !current) return;
    navigator.mediaSession.metadata = new MediaMetadata({
      title: current.track_name,
      artist: current.artist_names.join(", ") || current.artist_name,
      album: current.album_name,
      artwork: current.has_art
        ? [{ src: withToken(`/api/tracks/${current.track_rowid}/art`), sizes: "512x512" }]
        : [],
    });
  }, [current]);

  useEffect(() => {
    if (!("mediaSession" in navigator)) return;
    const handlers: [MediaSessionAction, MediaSessionActionHandler][] = [
      ["play", () => void audio.current?.play()],
      ["pause", () => audio.current?.pause()],
      ["nexttrack", next],
      ["previoustrack", previous],
    ];
    for (const [action, handler] of handlers) {
      try {
        navigator.mediaSession.setActionHandler(action, handler);
      } catch {
        /* not every browser implements every action */
      }
    }
  }, [next, previous]);

  useEffect(() => {
    if ("mediaSession" in navigator) {
      navigator.mediaSession.playbackState = playing ? "playing" : "paused";
    }
  }, [playing]);

  const api = useMemo<PlayerApi>(
    () => ({
      queue, index, current, playing, positionMs, durationMs, shuffle, repeat,
      restored, playQueue, enqueue, playNext, moveInQueue, removeFromQueue,
      clearQueue, toggle, next, previous, seek,
      toggleShuffle, cycleRepeat, rate,
      radio: radioSeed != null,
      radioBusy, radioNote, reasonsFor,
      mood, setMood, retryMuse, playingFrom, isMusePick,
    }),
    [queue, index, current, playing, positionMs, durationMs, shuffle, repeat,
     restored, playQueue, enqueue, playNext, moveInQueue, removeFromQueue,
     clearQueue, toggle, next, previous, seek,
     toggleShuffle, cycleRepeat, rate,
     radioSeed, radioBusy, radioNote,
     reasonsFor,
     mood, setMood, retryMuse, playingFrom, isMusePick],
  );

  return (
    <PlayerContext.Provider value={api}>
      {/* Rendered once, outside the router, for the lifetime of the app. */}
      <audio
        ref={audio}
        preload="metadata"
        onPlay={() => {
          setPlaying(true);
          attachGain(audio.current);
          beginSpan();
        }}
        onPause={() => setPlaying(false)}
        /* Deliberately no write on pause. A paused client does not write, and
         * labelling this one "playing" to sneak a final position through would
         * hand every background tab the same loophole. The cost is that a
         * pause loses up to ten seconds of position; the rule it protects is
         * worth more than that. */
        onTimeUpdate={(event) => {
          const ms = event.currentTarget.currentTime * 1000;
          // A ref as well as state: the heartbeat reads the position from a
          // closure that would otherwise be a render behind.
          positionRef.current = ms;
          setPositionMs(ms);
        }}
        onLoadedMetadata={(event) => {
          const seconds = event.currentTarget.duration;
          setDurationMs(Number.isFinite(seconds) ? seconds * 1000 : 0);
          // A restored session resumes here, not at zero.
          if (resumeAt.current != null) {
            event.currentTarget.currentTime = resumeAt.current / 1000;
            resumeAt.current = null;
          }
        }}
        onEnded={() => {
          // Closed here rather than left to the effect, because repeat-one
          // never changes track and the span must still end honestly.
          endSpan("track_end");
          seekCount.current = 0;
          positionRef.current = 0;

          if (repeat === "one" && audio.current) {
            audio.current.currentTime = 0;
            void audio.current.play(); // opens a fresh span
            return;
          }
          closingReason.current = "track_end";
          next();
        }}
      />
      {children}
    </PlayerContext.Provider>
  );
}
