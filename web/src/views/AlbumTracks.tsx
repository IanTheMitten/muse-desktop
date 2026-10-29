import { useMemo, useRef, useState } from "react";
import { SelectionBar } from "../components/SelectionBar";
import { useSelection } from "../components/selection";
import { useTrackActions } from "../components/TrackActions";
import { TrackMenu } from "../components/TrackMenu";
import { Link, useNavigate, useParams } from "react-router-dom";

import "../styles/library.css";
import { getAlbum, uploadAlbumArt } from "../api";
import { useJobs } from "../jobs/JobsContext";
import { BackIcon, PlayIcon, ShuffleIcon } from "../components/Icons";
import { CoverArt } from "../components/CoverArt";
import { TrackRow } from "../components/TrackRow";
import { playableOnly, unplayableReason } from "../player/formats";
import { useCardMesh } from "../components/Wash";
import { count, runtime } from "../format";
import { usePlayer } from "../player/PlayerContext";
import { useLoad } from "../useLoad";

export function AlbumTracks() {
  const { albumRowid = "" } = useParams();
  const navigate = useNavigate();
  const [version, setVersion] = useState(0);
  const { data, error, loading } = useLoad(
    (signal) => getAlbum(Number(albumRowid), signal),
    [albumRowid, version],
  );
  const { playQueue, current, playing, shuffle, toggleShuffle } = usePlayer();
  const rowids = useMemo(() => data?.tracks.map((t) => t.track_rowid) ?? [], [data]);
  const { selected, onSelect, clear } = useSelection(rowids);
  const { bumpArt } = useJobs();
  const fileRef = useRef<HTMLInputElement>(null);
  const [artError, setArtError] = useState<string | null>(null);
  const { run, dialogs } = useTrackActions(() => setVersion((v) => v + 1));

  const hasArt = data?.tracks.some((track) => track.has_art) ?? false;
  const artTrack = data?.tracks.find((track) => track.has_art)?.track_rowid ?? null;
  const glow = useCardMesh(artTrack, hasArt);

  // Tapping a track plays the album from there. Unplayable tracks are left out
  // of the queue rather than skipped over at playback time.
  const playable = playableOnly(data?.tracks ?? []);
  const from = { kind: "album" as const, label: data?.album_name ?? "" };

  const back = () =>
    window.history.state?.idx > 0 ? navigate(-1) : navigate("/library");

  const changeCover = async (file: File | undefined) => {
    if (!file || !data) return;
    setArtError(null);
    try {
      await uploadAlbumArt(data.album_rowid, file);
      bumpArt();
    } catch (cause) {
      setArtError(`Couldn't use that image: ${cause instanceof Error ? cause.message : String(cause)}`);
    }
  };

  const notFound = error?.includes("-> 404");

  return (
    <div className="view album-view">
      <button type="button" className="back" onClick={back}>
        <BackIcon aria-hidden="true" />
        Back
      </button>

      {error && (
        <p className="lib-error" role="alert">
          {notFound ? "That album is not in the library." : error}
        </p>
      )}
      {!error && loading && !data && <p className="lib-empty">Loading</p>}

      {data && (
        <div className="album">
          <header className="album-head">
            <div className="album-glow" style={{ background: glow }} aria-hidden="true" />
            <div className="album-art">
              <CoverArt
                src={`/api/albums/${data.album_rowid}/art`}
                hasArt={hasArt}
                size="hero"
                alt={`Cover of ${data.album_name}`}
              />
            </div>
            <div>
              <h1 className="page-title serif">{data.album_name}</h1>
              <p className="page-sub">
                <Link className="album-artist" to={`/artists/${encodeURIComponent(data.artist_name)}`}>
                  {data.artist_name}
                </Link>
                {` · ${count(data.track_count, "track")}`}
                {data.runtime_ms ? ` · ${runtime(data.runtime_ms)}` : ""}
              </p>
            </div>
            <div className="album-actions">
              <button
                type="button"
                className="btn primary"
                disabled={!playable.length}
                onClick={() => playQueue(playable, 0, from)}
              >
                <PlayIcon aria-hidden="true" />
                Play
              </button>
              <button
                type="button"
                className="btn"
                disabled={!playable.length}
                onClick={() => {
                  if (!shuffle) toggleShuffle();
                  playQueue(playable, Math.floor(Math.random() * playable.length), from);
                }}
              >
                <ShuffleIcon aria-hidden="true" />
                Shuffle
              </button>
              <button type="button" className="btn" onClick={() => fileRef.current?.click()}>
                Change cover
              </button>
              <input
                ref={fileRef}
                type="file"
                className="vh"
                accept="image/jpeg,image/png,image/webp"
                tabIndex={-1}
                aria-hidden="true"
                onChange={(event) => {
                  void changeCover(event.target.files?.[0]);
                  event.target.value = "";
                }}
              />
              {artError && <p className="media-note" role="alert">{artError}</p>}
            </div>
          </header>

          <ol className="tracks album-tracks">
            {data.tracks.map((track) => (
              <TrackRow
                key={track.track_rowid}
                n={track.track_no ?? undefined}
                title={track.track_name}
                durationMs={track.duration_ms}
                current={current?.track_rowid === track.track_rowid}
                paused={!playing}
                unplayable={unplayableReason(track)}
                selected={selected.has(track.track_rowid)}
                onSelect={(event) => onSelect(event, track.track_rowid)}
                trailing={<TrackMenu track={track} onAction={(action) => run(action, [track])} />}
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
      {data && selected.size > 0 && (
        <SelectionBar
          count={selected.size}
          onAction={(action) => run(action, data.tracks.filter((t) => selected.has(t.track_rowid)))}
          onClear={clear}
        />
      )}
      {dialogs}
    </div>
  );
}
