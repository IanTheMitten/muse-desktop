import { useEffect, useRef } from "react";

import { usePlayer } from "../player/PlayerContext";
import { shell, type NowState, type ShellCommand } from "../shell";

/* Answers the tray and the mini window for the main window. Renders nothing,
 * and does nothing in a plain browser. */
export function ShellBridge(): null {
  const { current, playing, toggle, next, previous } = usePlayer();
  const commands = useRef({ toggle, next, previous });
  commands.current = { toggle, next, previous };
  const now = useRef<NowState>({ track: null, playing: false });

  useEffect(() => {
    if (!shell) return;
    const state: NowState = {
      track: current
        ? {
            track_rowid: current.track_rowid,
            track_name: current.track_name,
            artist_name: current.artist_name,
            has_art: current.has_art,
          }
        : null,
      playing,
    };
    now.current = state;
    void shell.event.emitTo("mini", "muse:now", state);
  }, [current, playing]);

  useEffect(() => {
    if (!shell) return;
    const s = shell;
    const a = s.event.listen<ShellCommand>("muse:command", (e) => commands.current[e.payload]());
    const b = s.event.listen("muse:hello", () => void s.event.emitTo("mini", "muse:now", now.current));
    return () => {
      void a.then((un) => un());
      void b.then((un) => un());
    };
  }, []);

  return null;
}
