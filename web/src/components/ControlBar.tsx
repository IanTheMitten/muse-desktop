import { useId, useState } from "react";

import { CoverArt } from "./CoverArt";
import {
  LyricsIcon,
  MiniPlayerIcon,
  NextIcon,
  PauseIcon,
  PlayIcon,
  PrevIcon,
  QueueIcon,
} from "./Icons";
import { shell } from "../shell";
import { useCardMesh } from "./Wash";
import { duration } from "../format";
import { getVolume, setVolume } from "../player/gain";
import { usePlayer } from "../player/PlayerContext";
import { useSheet } from "../player/SheetContext";

/* Sits below the router but above the views, so it survives navigation for the
 * same reason the audio element does. One markup, three stages (shell.css):
 * a floating mini card on a phone, a docked bar from 600px, and from 1024px a
 * three-column bar with the full transport, a scrubber and the panel buttons.
 * Renders nothing until a record is loaded. */

export function ControlBar() {
  const { current, playing, positionMs, durationMs, toggle, next, previous, seek } = usePlayer();
  const { show } = useSheet();
  const tint = useCardMesh(current?.track_rowid ?? null, current?.has_art ?? false);
  const scrubId = useId();
  const [volume, setVol] = useState(getVolume);

  if (!current) return null;

  const total = durationMs || current.duration_ms || 0;
  const fraction = total > 0 ? Math.min(positionMs / total, 1) : 0;
  const PlayPause = playing ? PauseIcon : PlayIcon;
  const playLabel = playing ? "Pause" : "Play";

  return (
    <section className="player" aria-label="Player">
      <div className="player-tint" style={{ background: tint }} aria-hidden="true" />
      <button
        className="player-open"
        onClick={() => show()}
        aria-label={`Open now playing: ${current.track_name} by ${current.artist_name}`}
      >
        <span className="player-art">
          <CoverArt src={`/api/tracks/${current.track_rowid}/art`} hasArt={current.has_art} size="bar" />
        </span>
        <span className="player-meta">
          <span className="player-title">{current.track_name}</span>
          <span className="player-artist">{current.artist_name}</span>
        </span>
      </button>

      <div className="player-mid">
        <div className="transport">
          <button className="icon-btn" onClick={previous} aria-label="Previous">
            <PrevIcon />
          </button>
          <button className="icon-btn" onClick={toggle} aria-label={playLabel}>
            <PlayPause />
          </button>
          <button className="icon-btn" onClick={next} aria-label="Next">
            <NextIcon />
          </button>
        </div>
        <div className="scrub">
          <input
            id={scrubId}
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
      </div>

      <div className="player-controls compact">
        <button className="icon-btn" onClick={toggle} aria-label={playLabel}>
          <PlayPause />
        </button>
        <button className="icon-btn" onClick={next} aria-label="Next">
          <NextIcon />
        </button>
      </div>

      <div className="player-side">
        <input
          type="range"
          className="volume"
          min={0}
          max={100}
          value={Math.round(volume * 100)}
          style={{ "--p": `${volume * 100}%` } as React.CSSProperties}
          onChange={(e) => {
            const next = Number(e.target.value) / 100;
            setVol(next);
            setVolume(next);
          }}
          aria-label="Volume"
        />
        {shell && (
          <button
            className="icon-btn stroke"
            onClick={() => void shell?.core.invoke("open_mini")}
            aria-label="Open mini player"
          >
            <MiniPlayerIcon />
          </button>
        )}
        <button className="icon-btn stroke" onClick={() => show("lyrics")} aria-label="Lyrics">
          <LyricsIcon />
        </button>
        <button className="icon-btn stroke" onClick={() => show("queue")} aria-label="Up next">
          <QueueIcon />
        </button>
      </div>

      <div className="player-line" aria-hidden="true">
        <i style={{ transform: `scaleX(${fraction})` }} />
      </div>
    </section>
  );
}
