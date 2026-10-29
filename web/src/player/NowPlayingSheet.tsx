import { withToken } from "../token";
import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyEvent, PointerEvent as ReactPointerEvent } from "react";

import type { Track } from "../api";
import { CoverArt } from "../components/CoverArt";
import {
  CloseIcon,
  CollapseIcon,
  GripIcon,
  NextIcon,
  PauseIcon,
  PlayIcon,
  PrevIcon,
  RepeatIcon,
  ShuffleIcon,
} from "../components/Icons";
import { TrackRow } from "../components/TrackRow";
import { Wash } from "../components/Wash";
import { duration } from "../format";
import { LyricsPanel } from "../views/SyncedLyrics";
import { getVolume, setVolume } from "./gain";
import { unplayableReason } from "./formats";
import { usePlayer } from "./PlayerContext";
import { useSheet, type SheetOpen } from "./SheetContext";

/* The player, risen over the page.
 *
 * Always mounted and parked below the fold; opening removes a transform, so
 * only `transform` and `opacity` ever animate. Whether it is up is router
 * state (SheetContext), so every way out is `hide()`, which is history.back().
 * The record paints its own wash behind itself, and the cover scales down to
 * .92 while paused.
 */

const WIDE = "(min-width: 900px)";

function useWide(): boolean {
  const [wide, setWide] = useState(() => window.matchMedia(WIDE).matches);
  useEffect(() => {
    const mq = window.matchMedia(WIDE);
    const on = () => setWide(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return wide;
}

export function NowPlayingSheet() {
  const {
    current, playing, positionMs, durationMs, queue, index,
    toggle, next, previous, seek, rate, shuffle, repeat,
    toggleShuffle, cycleRepeat, playQueue, reasonsFor, playingFrom,
    moveInQueue, removeFromQueue,
  } = usePlayer();
  const { open, panel, hide, setPanel } = useSheet();
  const wide = useWide();
  const root = useRef<HTMLElement>(null);
  const inner = useRef<HTMLDivElement>(null);
  const closeBtn = useRef<HTMLButtonElement>(null);

  // Esc closes it, the same as the chevron. The timing dialog inside the
  // lyrics panel owns its own Esc.
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !document.querySelector(".lyrics-offset")) hide();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, hide]);

  // The page behind does not scroll; focus goes to the close button and comes
  // back to whatever opened the overlay.
  useEffect(() => {
    if (!open) return;
    const opener = document.activeElement as HTMLElement | null;
    document.body.classList.add("locked");
    closeBtn.current?.focus({ preventScroll: true });
    return () => {
      document.body.classList.remove("locked");
      opener?.focus?.({ preventScroll: true });
    };
  }, [open]);

  // A drag leaves an inline transform; opening or closing hands control back
  // to the class so the slide runs from wherever the finger let go.
  useEffect(() => {
    if (root.current) root.current.style.transform = "";
  }, [open]);

  // Drag down to close: touch only, phone only, never from the panel or an input.
  useEffect(() => {
    const el = root.current;
    if (!el) return;
    let y0: number | null = null;
    let t0 = 0;
    let dy = 0;
    const down = (e: PointerEvent) => {
      const target = e.target as Element;
      if (
        e.pointerType !== "touch" ||
        window.innerWidth >= 900 ||
        (inner.current?.scrollTop ?? 0) > 0 ||
        target.closest("input,.np-panel-wrap")
      ) return;
      y0 = e.clientY;
      t0 = performance.now();
      dy = 0;
    };
    const move = (e: PointerEvent) => {
      if (y0 === null) return;
      dy = Math.max(0, e.clientY - y0);
      if (dy > 6) {
        el.classList.add("dragging");
        el.style.transform = `translateY(${dy}px)`;
      }
    };
    const end = () => {
      if (y0 === null) return;
      el.classList.remove("dragging");
      const v = dy / Math.max(1, performance.now() - t0);
      y0 = null;
      if (dy > 140 || v > 0.7) hide();
      else el.style.transform = "";
    };
    el.addEventListener("pointerdown", down);
    el.addEventListener("pointermove", move);
    el.addEventListener("pointerup", end);
    el.addEventListener("pointercancel", end);
    return () => {
      el.removeEventListener("pointerdown", down);
      el.removeEventListener("pointermove", move);
      el.removeEventListener("pointerup", end);
      el.removeEventListener("pointercancel", end);
    };
  }, [hide, current === null]);

  if (!current) return null;

  const total = durationMs || current.duration_ms || 0;
  const fraction = total > 0 ? Math.min(positionMs / total, 1) : 0;
  const shown: SheetOpen = panel === "art" && wide ? "queue" : panel;
  const panelVisible = shown !== "art";
  const choose = (tab: "lyrics" | "queue") =>
    setPanel(!wide && shown === tab ? "art" : tab);

  const cls = ["np", open && "up", !playing && "paused", panelVisible && "panel-open"]
    .filter(Boolean)
    .join(" ");

  return (
    <section
      ref={root}
      className={cls}
      role="dialog"
      aria-modal="true"
      aria-label="Now playing"
      inert={!open}
    >
      <Wash where="np" />
      <div className="np-inner" ref={inner}>
        <span className="np-grab" aria-hidden="true" />
        <div className="np-top">
          <button
            ref={closeBtn}
            className="icon-btn stroke"
            onClick={hide}
            aria-label="Close now playing"
          >
            <CollapseIcon />
          </button>
          <div className="np-from">
            <span>Playing from</span>
            <b>{current.album_name}</b>
          </div>
          <Tabs shown={shown} onChoose={choose} className="side" />
          <span className="np-spacer" aria-hidden="true" />
        </div>

        <div className="np-body">
          <div className="np-main">
            <div className="np-art">
              <CoverArt
                src={withToken(`/api/tracks/${current.track_rowid}/art`)}
                hasArt={current.has_art}
                size="hero"
                alt={`${current.album_name} by ${current.artist_name}`}
              />
            </div>
            <div className="np-meta">
              <div className="np-names">
                <h2 className="np-title">{current.track_name}</h2>
                <div className="np-artist">{current.artist_name}</div>
              </div>
              <button
                className="icon-btn stroke"
                onClick={() => rate(1)}
                aria-label="Love this track"
                aria-pressed={current.rating === 1}
              >
                <svg viewBox="0 0 24 24" aria-hidden="true" className={current.rating === 1 ? "icon icon-fill" : "icon"}>
                  <path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z" />
                </svg>
              </button>
            </div>

            <div className="scrub">
              <input
                type="range"
                className="scrubber"
                min={0}
                max={1000}
                value={Math.round(fraction * 1000)}
                style={{ "--p": `${fraction * 100}%` } as React.CSSProperties}
                onChange={(e) => seek((Number(e.target.value) / 1000) * total)}
                aria-label="Position"
                aria-valuetext={`${duration(positionMs)} of ${duration(total)}`}
              />
              <div className="scrub-times" aria-hidden="true">
                <span>{duration(positionMs)}</span>
                <span>{duration(total)}</span>
              </div>
            </div>

            <div className="transport">
              <button
                className="icon-btn stroke"
                onClick={toggleShuffle}
                aria-label="Shuffle"
                aria-pressed={shuffle}
              >
                <ShuffleIcon />
              </button>
              <button className="icon-btn" onClick={previous} aria-label="Previous">
                <PrevIcon />
              </button>
              <button
                className="icon-btn big"
                onClick={toggle}
                aria-label={playing ? "Pause" : "Play"}
              >
                {playing ? <PauseIcon /> : <PlayIcon />}
              </button>
              <button className="icon-btn" onClick={next} aria-label="Next">
                <NextIcon />
              </button>
              <button
                className={repeat !== "off" ? "icon-btn stroke on" : "icon-btn stroke"}
                onClick={cycleRepeat}
                aria-label={`Repeat: ${repeat}`}
              >
                <RepeatIcon />
                {repeat === "one" && <span className="rep-one" aria-hidden="true">1</span>}
              </button>
            </div>

            {wide && open && <Volume />}
            <Tabs shown={shown} onChoose={choose} className="inline" />
          </div>

          {panelVisible && (
            <div className="np-panel-wrap">
              <Tabs shown={shown} onChoose={choose} className="inline sticky" onHide={() => setPanel("art")} />
              <div className="np-panel">
                {shown === "lyrics" ? (
                  <LyricsPanel />
                ) : (
                  <UpNext
                    albumName={current.album_name}
                    rows={queue.slice(index + 1, index + 81).map((track, k) => ({
                      track,
                      at: index + 1 + k,
                    }))}
                    isMuse={(rowid) => reasonsFor(rowid) != null}
                    onPlay={(at) => playQueue(queue, at, playingFrom ?? undefined)}
                    onMove={moveInQueue}
                    onRemove={removeFromQueue}
                  />
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

function Tabs({
  shown,
  onChoose,
  className,
  onHide,
}: {
  shown: SheetOpen;
  onChoose: (tab: "lyrics" | "queue") => void;
  className: string;
  onHide?: () => void;
}) {
  // "side" lives in the top row (wide only), "inline" under the transport
  // (phone only) and above the panel (wide only); np.css shows the right one.
  return (
    <div className={`np-tabs ${className}`} role="group" aria-label="Panel">
      {(["lyrics", "queue"] as const).map((tab) => (
        <button
          key={tab}
          type="button"
          aria-pressed={shown === tab}
          onClick={() => onChoose(tab)}
        >
          {tab === "lyrics" ? "Lyrics" : "Up next"}
        </button>
      ))}
      {onHide && (
        <button type="button" className="np-hide" onClick={onHide}>
          Hide
        </button>
      )}
    </div>
  );
}

function Volume() {
  const [v, setV] = useState(getVolume);
  return (
    <label className="np-volume">
      <span className="np-volume-label">Volume</span>
      <input
        type="range"
        min={0}
        max={100}
        value={Math.round(v * 100)}
        style={{ "--p": `${v * 100}%` } as React.CSSProperties}
        onChange={(e) => {
          const next = Number(e.target.value) / 100;
          setV(next);
          setVolume(next);
        }}
        aria-label="Volume"
      />
    </label>
  );
}

function UpNext({
  albumName,
  rows,
  isMuse,
  onPlay,
  onMove,
  onRemove,
}: {
  albumName: string;
  rows: { track: Track; at: number }[];
  isMuse: (rowid: number) => boolean;
  onPlay: (at: number) => void;
  onMove: (from: number, to: number) => void;
  onRemove: (at: number) => void;
}) {
  // After a keyboard move the row remounts under a new key; focus follows by index.
  const focusAt = useRef<number | null>(null);
  useEffect(() => {
    if (focusAt.current === null) return;
    document
      .querySelector<HTMLElement>(`.np .queue-grip[data-at="${focusAt.current}"]`)
      ?.focus({ preventScroll: true });
    focusAt.current = null;
  });
  if (rows.length === 0) {
    return <p className="lyrics-note">Nothing queued after this one.</p>;
  }
  const move = (from: number, to: number, focus: boolean) => {
    if (focus) focusAt.current = to;
    onMove(from, to);
  };
  const group = (label: string, list: typeof rows) =>
    list.length > 0 && (
      <>
        <h3 className="queue-h">{label}</h3>
        <QueueGroup list={list} onPlay={onPlay} onMove={move} onRemove={onRemove} />
      </>
    );
  return (
    <>
      {group(`Next from ${albumName}`, rows.filter((r) => !isMuse(r.track.track_rowid)))}
      {group("Then Muse continues with", rows.filter((r) => isMuse(r.track.track_rowid)))}
    </>
  );
}

type Drag = { i: number; target: number; dy: number };

function QueueGroup({
  list,
  onPlay,
  onMove,
  onRemove,
}: {
  list: { track: Track; at: number }[];
  onPlay: (at: number) => void;
  onMove: (from: number, to: number, focus: boolean) => void;
  onRemove: (at: number) => void;
}) {
  const [drag, setDrag] = useState<Drag | null>(null);
  const ul = useRef<HTMLUListElement>(null);
  const live = useRef<{ y0: number; h: number[] } | null>(null);

  const cancel = () => {
    live.current = null;
    setDrag(null);
  };
  // Esc drops the row back; it must not also close the sheet.
  useEffect(() => {
    if (!drag) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopImmediatePropagation();
      cancel();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [drag === null]);

  const start = (e: ReactPointerEvent<HTMLButtonElement>, i: number) => {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    const ul = e.currentTarget.closest("ul");
    if (!ul) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    const h = Array.from(ul.children).map((li) => li.getBoundingClientRect().height);
    live.current = { y0: e.clientY, h };
    setDrag({ i, target: i, dy: 0 });
  };
  const drift = (e: ReactPointerEvent<HTMLButtonElement>) => {
    if (!drag || !live.current) return;
    const { y0, h } = live.current;
    const before = h.slice(0, drag.i).reduce((a, b) => a + b, 0);
    const after = h.slice(drag.i + 1).reduce((a, b) => a + b, 0);
    const dy = Math.max(-before, Math.min(after, e.clientY - y0));
    let target = drag.i;
    let run = 0;
    if (dy > 0) {
      for (let j = drag.i + 1; j < h.length && dy > run + h[j] / 2; j++) {
        run += h[j];
        target = j;
      }
    } else {
      for (let j = drag.i - 1; j >= 0 && -dy > run + h[j] / 2; j--) {
        run += h[j];
        target = j;
      }
    }
    setDrag({ i: drag.i, target, dy });
  };
  const finish = () => {
    if (drag && drag.target !== drag.i) onMove(list[drag.i].at, list[drag.target].at, false);
    cancel();
  };
  const key = (e: ReactKeyEvent<HTMLButtonElement>, k: number) => {
    const to = k + (e.key === "ArrowUp" ? -1 : e.key === "ArrowDown" ? 1 : 0);
    if (to === k || to < 0 || to >= list.length) return;
    e.preventDefault();
    onMove(list[k].at, list[to].at, true);
  };
  // Rows the lifted one passes make room by one lifted-row height, transform
  // only. Set on the <li>s directly: TrackRow owns them and takes no style.
  useEffect(() => {
    const lis = Array.from(ul.current?.children ?? []) as HTMLElement[];
    lis.forEach((li, k) => {
      let y = 0;
      if (drag && live.current) {
        const { i, target, dy } = drag;
        const h = live.current.h[i];
        if (k === i) y = dy;
        else if (k > i && k <= target) y = -h;
        else if (k < i && k >= target) y = h;
      }
      li.style.transform = y ? `translateY(${y}px)` : "";
      li.classList.toggle("lifted", drag?.i === k);
    });
  }, [drag]);

  return (
    <ul className={drag ? "rows sorting" : "rows"} ref={ul}>
      {list.map(({ track, at }, k) => (
        <TrackRow
          key={`${track.track_rowid}-${at}`}
          variant="queue"
          title={track.track_name}
          sub={track.artist_name}
          durationMs={track.duration_ms}
          artSrc={track.has_art ? `/api/tracks/${track.track_rowid}/art` : null}
          unplayable={unplayableReason(track)}
          onPlay={() => onPlay(at)}
          leading={
            <button
              type="button"
              className="icon-btn queue-grip"
              data-at={at}
              aria-label={`Move ${track.track_name}`}
              onPointerDown={(e) => start(e, k)}
              onPointerMove={drift}
              onPointerUp={finish}
              onPointerCancel={cancel}
              onKeyDown={(e) => key(e, k)}
            >
              <GripIcon />
            </button>
          }
          trailing={
            <button
              type="button"
              className="icon-btn queue-remove"
              aria-label={`Remove ${track.track_name} from queue`}
              onClick={() => onRemove(at)}
            >
              <CloseIcon />
            </button>
          }
        />
      ))}
    </ul>
  );
}
