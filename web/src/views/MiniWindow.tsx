import { useEffect, useState } from "react";

import { CoverArt } from "../components/CoverArt";
import { CloseIcon, NextIcon, PauseIcon, PlayIcon, PrevIcon } from "../components/Icons";
import { useCardMesh } from "../components/Wash";
import { shell, type NowState, type ShellCommand } from "../shell";
import "../styles/mini.css";

/* The mini window is a remote: audio stays in the main window, this one mirrors
 * `muse:now` and sends `muse:command`. Deliberately no player or jobs context. */

const send = (command: ShellCommand) => void shell?.event.emitTo("main", "muse:command", command);
const close = () => void shell?.window.getCurrentWindow().close();

export function MiniWindow() {
  const [now, setNow] = useState<NowState>({ track: null, playing: false });
  const { track, playing } = now;
  const mesh = useCardMesh(track?.track_rowid ?? null, track?.has_art ?? false);

  useEffect(() => {
    if (!shell) return;
    let off: (() => void) | undefined;
    let live = true;
    void shell.event
      .listen<NowState>("muse:now", (e) => setNow(e.payload))
      .then((un) => {
        if (!live) return un();
        off = un;
        // Ask only once listening, so the reply cannot be missed.
        void shell?.event.emitTo("main", "muse:hello");
      });
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    window.addEventListener("keydown", onKey);
    return () => {
      live = false;
      off?.();
      window.removeEventListener("keydown", onKey);
    };
  }, []);

  const idle = track === null;

  return (
    <div className="mini" data-tauri-drag-region>
      <div className="mini-wash" style={{ background: mesh }} aria-hidden="true" />
      <button
        type="button"
        className="mini-art"
        aria-label="Show Muse"
        onClick={() => void shell?.core.invoke("show_main")}
      >
        <CoverArt
          src={track ? `/api/tracks/${track.track_rowid}/art` : null}
          hasArt={track?.has_art ?? false}
          size="bar"
        />
      </button>
      <div className="mini-text" data-tauri-drag-region>
        <p className="mini-title">{track ? track.track_name : "Nothing playing"}</p>
        {track && <p className="mini-artist">{track.artist_name}</p>}
      </div>
      <div className="mini-transport">
        <button type="button" className="mini-btn" aria-label="Previous" disabled={idle} onClick={() => send("previous")}>
          <PrevIcon />
        </button>
        <button
          type="button"
          className="mini-btn mini-play"
          aria-label={playing ? "Pause" : "Play"}
          disabled={idle}
          onClick={() => send("toggle")}
        >
          {playing ? <PauseIcon /> : <PlayIcon />}
        </button>
        <button type="button" className="mini-btn" aria-label="Next" disabled={idle} onClick={() => send("next")}>
          <NextIcon />
        </button>
      </div>
      <button type="button" className="mini-close" aria-label="Close mini player" onClick={close}>
        <CloseIcon />
      </button>
    </div>
  );
}
