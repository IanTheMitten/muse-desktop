import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  getSyncedLyrics,
  setLyricsOffset,
  type SyncedLyrics as SyncedLyricsResponse,
  type Track,
} from "../api";
import { usePlayer } from "../player/PlayerContext";
import { useLoad } from "../useLoad";

/* The design budget surface (PRD §682): typography, motion, restraint. Every
 * behavioural decision below is the PRD's, not a redesign -- this file only
 * decides what it looks like.
 *
 * `PlayerContext` gives a ~4Hz `positionMs` (it updates on the <audio>
 * element's `timeupdate`), which is too coarse for a smooth highlight on its
 * own. This view runs its own requestAnimationFrame loop that interpolates
 * from the last known position using wall-clock deltas, and re-anchors every
 * time `positionMs` actually ticks -- so drift never accumulates for more
 * than one tick's worth. */

/** A gap this long between two lines is a solo, not a broken sync (story 67). */
const INTERLUDE_MS = 5000;

/** How long auto-scroll stays suspended after the listener scrolls by hand
 *  (stories 64, 65). */
const AUTO_SCROLL_RESUME_MS = 4000;

/** How long a gap between scroll events has to be, once a programmatic
 *  scroll is in flight, before the next one is trusted to be the listener.
 *  Consecutive frames of a native smooth-scroll animation fire well inside
 *  this; a real gap this size only happens once the animation has actually
 *  stopped. */
const SCROLL_SETTLE_MS = 150;

/** A genuine press-and-hold, distinct from a tap-to-seek. */
const LONG_PRESS_MS = 500;

/** How far a touch can drift and still count as "holding still" -- real
 *  digitiser input never reports the exact same coordinate twice, and a
 *  zero-tolerance cancel would make the long-press fire close to never on
 *  real hardware. */
const LONG_PRESS_MOVE_TOLERANCE_PX = 10;

/** The nudge control's step (stories 68, 69). */
const OFFSET_STEP_MS = 100;

type Line = { ms: number; text: string };

/** The active line is the last one whose timestamp has passed. Binary search
 *  because this runs once a frame -- a linear scan over a five-minute song's
 *  worth of lines would be wasted work sixty times a second. `lines` is
 *  assumed sorted ascending, which is what the API guarantees. */
export function activeLineIndex(lines: Line[], positionMs: number): number {
  let lo = 0;
  let hi = lines.length - 1;
  let result = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (lines[mid].ms <= positionMs) {
      result = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return result;
}

export function LyricsPanel() {
  const { current, positionMs, durationMs, playing, seek } = usePlayer();
  if (!current) return <p className="lyrics-note">Nothing playing.</p>;
  return (
    // Keyed on the track: a new song is a new sync, a new offset, and a new
    // scroll position, and remounting gets all three for free instead of
    // hand-resetting a dozen refs in an effect.
    <LyricsBody
      key={current.track_rowid}
      track={current}
      positionMs={positionMs}
      durationMs={durationMs}
      playing={playing}
      seek={seek}
    />
  );
}

function LyricsBody({
  track,
  positionMs,
  durationMs,
  playing,
  seek,
}: {
  track: Track;
  positionMs: number;
  durationMs: number;
  playing: boolean;
  seek: (ms: number) => void;
}) {
  const { data, error, loading } = useLoad<SyncedLyricsResponse>(
    (signal) => getSyncedLyrics(track.track_rowid, signal),
    [track.track_rowid],
  );

  if (loading) return <p className="lyrics-note">Reading…</p>;

  if (error || !data) {
    // The generic `get<T>` throws `${path} -> ${status}` on any non-OK
    // response, so a 404 is told apart from a real failure by the status it
    // carries rather than by a second endpoint. A missing sync is a fact, not
    // a failure (PRODUCT.md's voice).
    const notFound = error?.includes("404") ?? false;
    return (
      <p className="lyrics-note">
        {notFound ? "No lyrics for this track." : "Could not read the lyrics."}
      </p>
    );
  }

  if (!data.synced) {
    return (
      <>
        {data.plain && <p className="lyrics-note">Not synced yet</p>}
        <PlainLyrics plain={data.plain} />
      </>
    );
  }

  return (
    <SyncedPanel
      trackRowid={track.track_rowid}
      rawLines={data.lines ?? []}
      initialOffsetMs={data.offset_ms}
      positionMs={positionMs}
      durationMs={durationMs}
      playing={playing}
      seek={seek}
    />
  );
}

/** No `.lrc`, no timings, no highlighting -- and no invented ones either.
 *  Interpolating fake timestamps from line count and duration would look
 *  synced right up until it very visibly wasn't; a static page is honest
 *  about what the data actually is (story 70). */
function PlainLyrics({ plain }: { plain: string | null }) {
  const lines = useMemo(() => (plain ?? "").split(/\r?\n/), [plain]);
  return (
    <div className="lyrics-scroll lyrics-plain">
      {lines.length === 0 || (lines.length === 1 && lines[0] === "") ? (
        <p className="lyrics-note">No lyrics for this track.</p>
      ) : (
        lines.map((line, i) =>
          line.trim() === "" ? (
            <br key={i} />
          ) : (
            <p className="lyrics-plain-line" key={i}>
              {line}
            </p>
          ),
        )
      )}
    </div>
  );
}

function formatOffsetMs(ms: number): string {
  if (ms === 0) return "0 ms";
  // U+2212 (minus sign) reads better next to a mono figure than a hyphen.
  return `${ms > 0 ? "+" : "−"}${Math.abs(ms)} ms`;
}

function SyncedPanel({
  trackRowid,
  rawLines,
  initialOffsetMs,
  positionMs,
  durationMs,
  playing,
  seek,
}: {
  trackRowid: number;
  rawLines: Line[];
  initialOffsetMs: number;
  positionMs: number;
  durationMs: number;
  playing: boolean;
  seek: (ms: number) => void;
}) {
  // `rawLines` from the API is offset-free; the correction is applied once,
  // here, rather than re-fetched every time the listener nudges it. Clamped
  // at 0 -- a large enough negative nudge on an early line would otherwise
  // produce a negative timestamp, and tapping that line to seek would hand
  // the player a position before the track starts instead of "the start".
  const [offsetMs, setOffsetMs] = useState(initialOffsetMs);
  const lines = useMemo<Line[]>(
    () =>
      rawLines.map((line) => ({
        ms: Math.max(0, line.ms + offsetMs),
        text: line.text,
      })),
    [rawLines, offsetMs],
  );

  const [activeIndex, setActiveIndex] = useState(-1);
  const [autoScroll, setAutoScroll] = useState(true);
  const [nudgeOpen, setNudgeOpen] = useState(false);

  const scrollRef = useRef<HTMLDivElement | null>(null);
  const lineRefs = useRef(new Map<number, HTMLButtonElement>());
  const gapRefs = useRef(new Map<number, HTMLDivElement>());

  // Read fresh inside the rAF loop without making the loop's effect restart
  // every time one of these changes -- the loop itself only needs to start
  // once per track and keep reading current values through refs.
  const linesRef = useRef(lines);
  linesRef.current = lines;
  const activeIndexRef = useRef(activeIndex);
  activeIndexRef.current = activeIndex;
  const playingRef = useRef(playing);
  playingRef.current = playing;
  const durationRef = useRef(durationMs);
  durationRef.current = durationMs;

  // The interpolation anchor: the last real position PlayerContext reported,
  // and the wall-clock moment it reported it. Every `positionMs` tick moves
  // this forward, which is what keeps the rAF-driven estimate from drifting.
  const anchorMsRef = useRef(positionMs);
  const anchorTsRef = useRef(performance.now());
  useEffect(() => {
    anchorMsRef.current = positionMs;
    anchorTsRef.current = performance.now();
  }, [positionMs]);

  // Scrolling this ourselves fires a native `scroll` event indistinguishable
  // from the listener's own -- and a fixed guess at how long the browser's
  // `scroll-behavior: smooth` animation takes is exactly that, a guess, that
  // a long jump (tap-to-seek across a five-minute song) could outlast. This
  // debounces instead: every scroll event received while a programmatic
  // scroll is in flight re-arms a short settle timer rather than checking a
  // deadline, so however long the animation actually runs, the *next* event
  // after it truly stops is the first one that can possibly be the listener.
  const isProgrammaticScroll = useRef(false);
  const settleTimer = useRef<number | null>(null);
  const resumeTimer = useRef<number | null>(null);

  const armSettleTimer = useCallback(() => {
    if (settleTimer.current != null) window.clearTimeout(settleTimer.current);
    settleTimer.current = window.setTimeout(() => {
      isProgrammaticScroll.current = false;
    }, SCROLL_SETTLE_MS);
  }, []);

  const scrollToIndex = useCallback(
    (index: number) => {
      const container = scrollRef.current;
      const line = lineRefs.current.get(index);
      if (!container || !line) return;
      const containerRect = container.getBoundingClientRect();
      const lineRect = line.getBoundingClientRect();
      // A fixed 40% down from the top -- above centre, because the eye reads
      // down the page toward what comes next.
      const target = containerRect.top + containerRect.height * 0.4;
      const delta = lineRect.top + lineRect.height / 2 - target;
      // Nothing to scroll, so no `scroll` event to arrive and clear the flag
      // -- arming it here would leave it stuck true for the rest of the
      // session with nothing left to un-stick it.
      if (Math.abs(delta) < 1) return;
      isProgrammaticScroll.current = true;
      // Armed immediately, not only from the first `scroll` event: a delta
      // small enough that the browser coalesces or skips firing one at all
      // would otherwise leave the flag stuck true with no timer running to
      // clear it, silently breaking user-scroll detection for the rest of
      // the session.
      armSettleTimer();
      // Reduced motion jumps instead of gliding (story 76).
      const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      container.scrollTo({
        top: container.scrollTop + delta,
        behavior: reduced ? "instant" : "smooth",
      });
    },
    [armSettleTimer],
  );

  const resumeAutoScrollAfterDelay = useCallback(() => {
    if (resumeTimer.current != null) window.clearTimeout(resumeTimer.current);
    resumeTimer.current = window.setTimeout(() => {
      setAutoScroll(true);
    }, AUTO_SCROLL_RESUME_MS);
  }, []);

  const handleContainerScroll = useCallback(() => {
    if (isProgrammaticScroll.current) {
      armSettleTimer();
      return;
    }
    setAutoScroll(false);
    resumeAutoScrollAfterDelay();
  }, [armSettleTimer, resumeAutoScrollAfterDelay]);

  useEffect(
    () => () => {
      if (resumeTimer.current != null) window.clearTimeout(resumeTimer.current);
      if (settleTimer.current != null) window.clearTimeout(settleTimer.current);
    },
    [],
  );

  useEffect(() => {
    if (autoScroll) scrollToIndex(activeIndex);
  }, [activeIndex, autoScroll, scrollToIndex]);

  const jumpToCurrent = useCallback(() => {
    if (resumeTimer.current != null) window.clearTimeout(resumeTimer.current);
    setAutoScroll(true);
  }, []);

  // --- the clock: rAF interpolation between `timeupdate` ticks -----------
  useEffect(() => {
    if (lines.length === 0) return;
    let frame: number;
    const tick = () => {
      const currentLines = linesRef.current;
      const now = performance.now();
      const elapsed = playingRef.current ? now - anchorTsRef.current : 0;
      let pos = anchorMsRef.current + elapsed;
      const duration = durationRef.current;
      if (duration > 0) pos = Math.min(pos, duration);
      pos = Math.max(pos, 0);

      const idx = activeLineIndex(currentLines, pos);
      if (idx !== activeIndexRef.current) {
        activeIndexRef.current = idx;
        setActiveIndex(idx);
      }

      // The interlude fill is written straight to the DOM node instead of
      // through React state: it would otherwise change every frame while a
      // gap is open, and routing that through a render buys nothing the
      // binary search above doesn't already give for free.
      if (idx >= 0 && idx < currentLines.length - 1) {
        const gapStart = currentLines[idx].ms;
        const gapEnd = currentLines[idx + 1].ms;
        const gap = gapEnd - gapStart;
        if (gap > INTERLUDE_MS) {
          const fill = gapRefs.current.get(idx);
          if (fill) {
            const progress = Math.min(Math.max((pos - gapStart) / gap, 0), 1);
            fill.style.setProperty("--gap-fill", String(progress));
          }
        }
      }

      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
    // `lines.length` rather than `lines`: an offset nudge changes every
    // timestamp but not the count, and the loop already reads the latest
    // array through `linesRef` on every frame -- restarting it on a nudge
    // would just be a dropped frame for no benefit.
  }, [lines.length]);

  // --- long-press / right-click opens the offset nudge (stories 68, 69) --
  const suppressClick = useRef(false);
  const pressTimer = useRef<number | null>(null);
  const pressOrigin = useRef<{ x: number; y: number } | null>(null);

  const clearPressTimer = useCallback(() => {
    if (pressTimer.current != null) {
      window.clearTimeout(pressTimer.current);
      pressTimer.current = null;
    }
    pressOrigin.current = null;
  }, []);

  const handlePointerDown = useCallback(
    (event: React.PointerEvent) => {
      suppressClick.current = false;
      clearPressTimer();
      pressOrigin.current = { x: event.clientX, y: event.clientY };
      pressTimer.current = window.setTimeout(() => {
        suppressClick.current = true;
        setNudgeOpen(true);
      }, LONG_PRESS_MS);
    },
    [clearPressTimer],
  );

  // A finger travelling is scrolling, not holding still to nudge -- but a
  // real touch is never perfectly still either, and cancelling on the first
  // pixel of digitiser noise would make the long-press fire close to never
  // on real hardware. `LONG_PRESS_MOVE_TOLERANCE_PX` is the difference
  // between tremor and an actual scroll gesture.
  const handlePointerMove = useCallback(
    (event: React.PointerEvent) => {
      const origin = pressOrigin.current;
      if (!origin) return;
      const dx = event.clientX - origin.x;
      const dy = event.clientY - origin.y;
      if (Math.hypot(dx, dy) > LONG_PRESS_MOVE_TOLERANCE_PX) {
        clearPressTimer();
      }
    },
    [clearPressTimer],
  );

  const handlePointerRelease = clearPressTimer;

  const nudge = useCallback(
    (deltaMs: number) => {
      setOffsetMs((previous) => {
        const next = previous + deltaMs;
        setLyricsOffset(trackRowid, next).catch(() => {
          // A rare correction (O11) that failed to save is not worth
          // interrupting playback for -- it still applies locally this
          // session, and the listener can try again later.
        });
        return next;
      });
    },
    [trackRowid],
  );

  useEffect(() => {
    if (!nudgeOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setNudgeOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [nudgeOpen]);

  return (
    <div className="lyrics-host">
      <div
        ref={scrollRef}
        className="lyrics-scroll"
        onScroll={handleContainerScroll}
        onPointerDown={handlePointerDown}
        onPointerUp={handlePointerRelease}
        onPointerCancel={handlePointerRelease}
        onPointerMove={handlePointerMove}
        onContextMenu={(event) => {
          event.preventDefault();
          clearPressTimer();
          setNudgeOpen(true);
        }}
      >
        {lines.map((line, i) => {
          // Before playback reaches the first line, nothing is "near" yet --
          // otherwise line 0 would read as dominant before it has been sung.
          const distance = activeIndex < 0 ? 2 : Math.abs(i - activeIndex);
          const tier = distance === 0 ? "active" : distance === 1 ? "near" : "far";
          const gapAfter = i < lines.length - 1 ? lines[i + 1].ms - line.ms : 0;
          return (
            <div className="lyrics-line-row" key={`${line.ms}:${i}`}>
              <button
                ref={(el) => {
                  if (el) lineRefs.current.set(i, el);
                  else lineRefs.current.delete(i);
                }}
                type="button"
                className={`lyrics-line lyrics-line-${tier}${
                  activeIndex >= 0 && i < activeIndex ? " is-past" : ""
                }`}
                aria-current={tier === "active" ? "true" : undefined}
                onClick={() => {
                  if (suppressClick.current) {
                    suppressClick.current = false;
                    return;
                  }
                  seek(line.ms);
                }}
              >
                {tier === "active" && <span className="lyrics-line-mark" aria-hidden="true" />}
                {/* An empty line in an .lrc file is a beat, not a missing
                    word -- shown as one rather than collapsing to nothing,
                    which would look like a skipped line. */}
                {line.text || "♪"}
              </button>
              {gapAfter > INTERLUDE_MS && (
                <div
                  ref={(el) => {
                    if (el) gapRefs.current.set(i, el);
                    else gapRefs.current.delete(i);
                  }}
                  className={
                    activeIndex > i ? "lyrics-gap lyrics-gap-done" : "lyrics-gap"
                  }
                  aria-hidden="true"
                >
                  <div className="lyrics-gap-fill" />
                </div>
              )}
            </div>
          );
        })}
      </div>

      {!autoScroll && (
        <button className="lyrics-jump" onClick={jumpToCurrent}>
          Jump to current <span aria-hidden="true">↓</span>
        </button>
      )}

      {nudgeOpen && (
        <div className="lyrics-offset-backdrop" onClick={() => setNudgeOpen(false)}>
          <div
            className="lyrics-offset"
            role="dialog"
            aria-label="Lyrics timing"
            onClick={(event) => event.stopPropagation()}
          >
            <p className="lyrics-offset-label">Timing</p>
            <div className="lyrics-offset-controls">
              <button
                type="button"
                onClick={() => nudge(-OFFSET_STEP_MS)}
                aria-label={`Shift lyrics ${OFFSET_STEP_MS} milliseconds earlier`}
              >
                {"−"}{OFFSET_STEP_MS}
              </button>
              <span className="lyrics-offset-value num">{formatOffsetMs(offsetMs)}</span>
              <button
                type="button"
                onClick={() => nudge(OFFSET_STEP_MS)}
                aria-label={`Shift lyrics ${OFFSET_STEP_MS} milliseconds later`}
              >
                +{OFFSET_STEP_MS}
              </button>
            </div>
            <button
              type="button"
              className="lyrics-offset-done"
              onClick={() => setNudgeOpen(false)}
            >
              Done
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
