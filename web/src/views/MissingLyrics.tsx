import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { getMissingLyrics, markNoWords, type Track } from "../api";
import "../styles/library.css";
import { LyricsEditDialog } from "../components/LyricsEditDialog";
import { Screen } from "../components/Screen";
import { TrackRow } from "../components/TrackRow";
import { count } from "../format";
import { useJobs } from "../jobs/JobsContext";
import { usePlayer } from "../player/PlayerContext";
import { playableOnly, unplayableReason } from "../player/formats";

/* Songs the lyrics lookup found nothing for. Adding words or marking a song
 * wordless both take it off this list. */
export function MissingLyrics() {
  const { libraryVersion } = useJobs();
  const { playQueue, current, playing } = usePlayer();
  const [tracks, setTracks] = useState<Track[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<Track | null>(null);
  const [status, setStatus] = useState("");
  const [version, setVersion] = useState(0);

  useEffect(() => {
    const ac = new AbortController();
    getMissingLyrics(ac.signal).then(
      (r) => {
        setTracks(r.tracks);
        setError(null);
      },
      (cause: unknown) => {
        if (!ac.signal.aborted) setError(cause instanceof Error ? cause.message : String(cause));
      },
    );
    return () => ac.abort();
  }, [libraryVersion, version]);

  const noWords = (track: Track) =>
    markNoWords(track.track_rowid).then(
      () => {
        setTracks((rows) => rows && rows.filter((t) => t.track_rowid !== track.track_rowid));
        setStatus(`Marked ${track.track_name} as having no words.`);
      },
      (cause: unknown) => setError(cause instanceof Error ? cause.message : String(cause)),
    );

  const playable = tracks ? playableOnly(tracks) : [];

  return (
    <Screen
      title="Missing lyrics"
      subtitle={tracks ? count(tracks.length, "song") : undefined}
      error={error}
      loading={!tracks}
    >
      {tracks && (
        <>
          {tracks.length > 0 && (
            <p className="ml-lede">
              Muse looked these up and found nothing. Paste the words, or mark a song as
              having none.
            </p>
          )}
          <p className="vh" role="status">{status}</p>
          {tracks.length ? (
            <div className="section">
              <ul className="tracks song-list ml-list">
                {tracks.map((track) => (
                  <TrackRow
                    key={track.track_rowid}
                    variant="song"
                    title={track.track_name}
                    sub={`${track.artist_name} · ${track.album_name}`}
                    durationMs={track.duration_ms}
                    artSrc={track.has_art ? `/api/tracks/${track.track_rowid}/art` : null}
                    current={current?.track_rowid === track.track_rowid}
                    paused={!playing}
                    unplayable={unplayableReason(track)}
                    onPlay={() =>
                      playQueue(
                        playable,
                        playable.findIndex((t) => t.track_rowid === track.track_rowid),
                        { kind: "track", label: "Missing lyrics" },
                      )
                    }
                    trailing={
                      <div className="ml-actions">
                        <button type="button" className="btn" onClick={() => setEditing(track)}>
                          Add lyrics
                        </button>
                        <button
                          type="button"
                          className="link-btn"
                          aria-label={`Song has no words: ${track.track_name}`}
                          onClick={() => void noWords(track)}
                        >
                          No words
                        </button>
                      </div>
                    }
                  />
                ))}
              </ul>
            </div>
          ) : (
            <p className="lib-empty">
              Every song has lyrics or is marked as having none.{" "}
              <Link className="link-btn" to="/library">Back to the library</Link>
            </p>
          )}
        </>
      )}
      {editing && (
        <LyricsEditDialog
          track={editing}
          onClose={() => setEditing(null)}
          onSaved={() => setVersion((v) => v + 1)}
        />
      )}
    </Screen>
  );
}
