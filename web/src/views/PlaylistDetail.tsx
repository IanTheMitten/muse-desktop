import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import "../styles/library.css";
import "../styles/playlists.css";
import { deletePlaylist, getPlaylist, renamePlaylist } from "../api";
import { BackIcon, PlayIcon, ShuffleIcon } from "../components/Icons";
import { TrackRow } from "../components/TrackRow";
import { unplayableReason } from "../player/formats";
import { usePlayer } from "../player/PlayerContext";
import { useLoad } from "../useLoad";
import { Mosaic, playlistMeta } from "./Playlists";

export function PlaylistDetail() {
  const { playlistId = "" } = useParams();
  const id = Number(playlistId);
  const navigate = useNavigate();
  const { data, error, loading } = useLoad((signal) => getPlaylist(id, signal), [id]);
  const { playQueue, current, playing, shuffle, toggleShuffle } = usePlayer();

  const [name, setName] = useState<string | null>(null); // non-null while renaming
  const [title, setTitle] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (data) setTitle(data.name);
  }, [data]);

  const playable = data?.tracks.filter((t) => t.playable) ?? [];
  const from = { kind: "playlist" as const, label: title, playlistId: id };
  const back = () =>
    window.history.state?.idx > 0 ? navigate(-1) : navigate("/playlists");
  const notFound = error?.includes("-> 404");

  const save = async () => {
    const next = (name ?? "").trim();
    if (!next) return setProblem("Give the playlist a name.");
    setBusy(true);
    try {
      const updated = await renamePlaylist(id, next);
      setTitle(updated.name);
      setName(null);
      setProblem(null);
    } catch {
      setProblem("Could not rename. Try again.");
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    setBusy(true);
    try {
      await deletePlaylist(id);
      navigate("/playlists", { replace: true });
    } catch {
      setProblem("Could not delete. Try again.");
      setBusy(false);
    }
  };

  return (
    <div className="view">
      <button type="button" className="back" onClick={back}>
        <BackIcon aria-hidden="true" />
        Back
      </button>

      {error && (
        <p className="lib-error" role="alert">
          {notFound ? "That playlist does not exist." : error}
        </p>
      )}
      {!error && loading && !data && <p className="lib-empty">Loading</p>}

      {data && (
        <div className="album">
          <header className="pl-head">
            <Mosaic ids={data.cover_track_rowids} />
            {name === null ? (
              <div>
                <h1 className="page-title serif">{title}</h1>
                <p className="page-sub">{playlistMeta(data)}</p>
              </div>
            ) : (
              <form
                className="pl-rename field"
                onSubmit={(e) => {
                  e.preventDefault();
                  void save();
                }}
              >
                <div>
                  <label htmlFor="pl-name">Playlist name</label>
                  <input
                    id="pl-name"
                    value={name}
                    autoFocus
                    aria-invalid={problem ? true : undefined}
                    aria-describedby={problem ? "pl-problem" : undefined}
                    onChange={(e) => setName(e.target.value)}
                  />
                </div>
                {problem && <p id="pl-problem" className="error" role="alert">{problem}</p>}
                <div className="pl-actions">
                  <button type="submit" className="btn primary" disabled={busy}>Save</button>
                  <button
                    type="button"
                    className="btn"
                    onClick={() => {
                      setName(null);
                      setProblem(null);
                    }}
                  >
                    Cancel
                  </button>
                </div>
              </form>
            )}

            {name === null && !confirming && (
              <div className="pl-actions">
                <button type="button" className="btn primary" disabled={!playable.length}
                  onClick={() => playQueue(playable, 0, from)}>
                  <PlayIcon aria-hidden="true" />
                  Play
                </button>
                <button type="button" className="btn" disabled={!playable.length}
                  onClick={() => {
                    if (!shuffle) toggleShuffle();
                    playQueue(playable, Math.floor(Math.random() * playable.length), from);
                  }}>
                  <ShuffleIcon aria-hidden="true" />
                  Shuffle
                </button>
                <button type="button" className="btn" onClick={() => { setName(title); setProblem(null); }}>
                  Rename
                </button>
                <button type="button" className="btn" onClick={() => { setConfirming(true); setProblem(null); }}>
                  Delete
                </button>
              </div>
            )}

            {confirming && (
              <div className="pl-confirm" role="group" aria-label="Delete playlist">
                <p>Delete this playlist?</p>
                {problem && <p className="error" role="alert">{problem}</p>}
                <div className="pl-actions">
                  <button type="button" className="btn primary" disabled={busy} onClick={() => void remove()}>
                    Delete
                  </button>
                  <button type="button" className="btn" onClick={() => setConfirming(false)}>
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </header>

          <ol className="tracks album-tracks">
            {data.tracks.map((track, i) => (
              <TrackRow
                key={`${track.track_rowid}-${i}`}
                n={i + 1}
                title={track.track_name}
                sub={track.artist_name}
                durationMs={track.duration_ms}
                current={current?.track_rowid === track.track_rowid}
                paused={!playing}
                unplayable={unplayableReason(track)}
                onPlay={() =>
                  playQueue(
                    playable,
                    playable.findIndex((t) => t.track_rowid === track.track_rowid),
                    from,
                  )
                }
              />
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}
