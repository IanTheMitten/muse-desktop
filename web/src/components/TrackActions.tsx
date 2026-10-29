import { useState, type ReactNode } from "react";
import type { Track } from "../api";
import { playableOnly } from "../player/formats";
import { usePlayer } from "../player/PlayerContext";
import { AddToPlaylistDialog } from "./AddToPlaylistDialog";
import { EditDetailsDialog } from "./EditDetailsDialog";
import { LyricsEditDialog } from "./LyricsEditDialog";
import { RemoveDialog } from "./RemoveDialog";
import type { TrackAction } from "./TrackMenu";

type Open = { action: "playlist" | "details" | "lyrics" | "remove"; tracks: Track[] } | null;

export function useTrackActions(onChanged: () => void): {
  run: (action: TrackAction, tracks: Track[]) => void;
  dialogs: ReactNode;
} {
  const { playNext, enqueue } = usePlayer();
  const [open, setOpen] = useState<Open>(null);
  const close = () => setOpen(null);

  function run(action: TrackAction, tracks: Track[]) {
    if (action === "next") playNext(playableOnly(tracks));
    else if (action === "queue") enqueue(playableOnly(tracks));
    else setOpen({ action, tracks });
  }

  let dialogs: ReactNode = null;
  if (open?.action === "playlist") {
    dialogs = <AddToPlaylistDialog tracks={open.tracks} onClose={close} onAdded={() => {}} />;
  } else if (open?.action === "details") {
    dialogs = <EditDetailsDialog tracks={open.tracks} onClose={close} onSaved={() => { onChanged(); close(); }} />;
  } else if (open?.action === "lyrics") {
    dialogs = <LyricsEditDialog track={open.tracks[0]} onClose={close} onSaved={() => {}} />;
  } else if (open?.action === "remove") {
    dialogs = <RemoveDialog tracks={open.tracks} onClose={close} onRemoved={onChanged} />;
  }
  return { run, dialogs };
}
