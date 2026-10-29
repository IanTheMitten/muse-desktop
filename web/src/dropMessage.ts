import type { ImportResult } from "./api";

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

export function dropMessage(r: ImportResult): string {
  const parts = [
    r.folders_added > 0 ? plural(r.folders_added, "folder") : "",
    r.songs_added > 0 ? plural(r.songs_added, "song") : "",
  ].filter(Boolean);
  if (parts.length) return `Added ${parts.join(" and ")}`;
  if (r.already > 0) return "Already in your library";
  return "Nothing to add. Muse plays MP3, M4A, FLAC, WAV, OGG and Opus files.";
}
