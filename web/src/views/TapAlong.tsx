import { useEffect, useRef, useState } from "react";
import { saveTappedTimings } from "../api";
import { cueIndex, isDone, startTap, tap, undo, type TapState } from "../player/tapAlong";

/** "0:42.1" -- tenths, truncated so a stamp never reads later than it is. */
function stampText(ms: number): string {
  const t = Math.floor(ms / 100);
  const s = Math.floor(t / 10);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}.${t % 10}`;
}

export function TapAlong({
  lines,
  trackRowid,
  positionMs,
  playing,
  seek,
  toggle,
  onCancel,
  onSaved,
}: {
  lines: string[];
  trackRowid: number;
  positionMs: number;
  playing: boolean;
  seek: (ms: number) => void;
  toggle: () => void;
  onCancel: () => void;
  onSaved: () => void;
}) {
  const [state, setState] = useState<TapState>(() => startTap(lines.length));
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState(false);
  const cueRef = useRef<HTMLParagraphElement | null>(null);

  // positionMs ticks at ~4Hz; interpolate so a tap lands on the wall clock.
  const anchor = useRef({ ms: positionMs, at: performance.now() });
  useEffect(() => {
    anchor.current = { ms: positionMs, at: performance.now() };
  }, [positionMs]);
  const playingRef = useRef(playing);
  playingRef.current = playing;
  const stateRef = useRef(state);
  stateRef.current = state;
  const savingRef = useRef(saving);
  savingRef.current = saving;

  useEffect(() => {
    seek(0);
    anchor.current = { ms: 0, at: performance.now() };
    if (!playingRef.current) toggle();
    // Mount only: entering the mode is the one time we move the transport.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const press = () => {
    if (savingRef.current) return;
    const a = anchor.current;
    const now = a.ms + (playingRef.current ? performance.now() - a.at : 0);
    setState((s) => tap(s, now));
  };
  const back = () => !savingRef.current && setState((s) => undo(s));
  const restart = () => setState(startTap(lines.length));

  useEffect(() => {
    const ignored = (e: KeyboardEvent) =>
      e.target instanceof Element && !!e.target.closest("input, textarea, dialog");
    const onKey = (e: KeyboardEvent) => {
      if (ignored(e)) return;
      if (e.key === " ") {
        e.preventDefault();
        if (!e.repeat && !isDone(stateRef.current)) press();
      } else if (e.key === "Backspace") {
        e.preventDefault();
        back();
      }
    };
    // Capture + stopImmediatePropagation: Esc leaves the mode, not Now Playing.
    const onEsc = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopImmediatePropagation();
      onCancel();
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("keydown", onEsc, true);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("keydown", onEsc, true);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onCancel]);

  const cue = cueIndex(state);
  const done = isDone(state);
  useEffect(() => {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    cueRef.current?.scrollIntoView({ block: "center", behavior: reduced ? "instant" : "smooth" });
  }, [cue]);

  const save = () => {
    setSaving(true);
    setFailed(false);
    saveTappedTimings(trackRowid, state.stamps).then(
      onSaved,
      () => {
        setSaving(false);
        setFailed(true);
      },
    );
  };

  return (
    <div className="tap">
      <header className="tap-head">
        <p className="tap-eyebrow">Tap along</p>
        <p className="tap-count num" aria-live="polite">
          {done ? `All ${state.total} lines` : `Line ${cue + 1} of ${state.total}`}
        </p>
      </header>
      <div className="tap-score">
        {lines.map((text, i) => {
          const kind = i < cue ? "stamped" : i === cue ? "cue" : "upcoming";
          return (
            <p
              key={i}
              ref={i === cue ? cueRef : undefined}
              className={`tap-line tap-${kind}`}
              aria-current={kind === "cue" ? "true" : undefined}
            >
              {kind === "cue" && <span className="lyrics-line-mark" aria-hidden="true" />}
              {kind === "stamped" && <span className="tap-stamp num">{stampText(state.stamps[i])}</span>}
              {text || "♪"}
            </p>
          );
        })}
      </div>
      <div className="tap-controls">
        {done ? (
          <button type="button" className="tap-pill" onClick={save} disabled={saving}>
            Save timings
          </button>
        ) : (
          <button type="button" className="tap-pill" onClick={press}>
            Tap
          </button>
        )}
        {failed && <p className="error" role="alert">Couldn't save the timings.</p>}
        {!done && <p className="tap-hint">Press Space or tap as each line starts</p>}
        <div className="tap-row">
          <button type="button" className="tap-sec" onClick={back} disabled={saving || state.stamps.length === 0}>
            Undo
          </button>
          {!done && (
            <button type="button" className="tap-sec" onClick={restart} disabled={state.stamps.length === 0}>
              Start over
            </button>
          )}
          <button type="button" className="tap-sec" onClick={onCancel} disabled={saving}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
