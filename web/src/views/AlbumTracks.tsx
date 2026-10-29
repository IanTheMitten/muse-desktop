import { withToken } from "../token";
import { Link, useNavigate, useParams } from "react-router-dom";

import "../styles/library.css";
import { getAlbum } from "../api";
import { BackIcon, PlayIcon, ShuffleIcon } from "../components/Icons";
import { CoverArt } from "../components/CoverArt";
import { TrackRow } from "../components/TrackRow";
import { useCardMesh } from "../components/Wash";
import { count, runtime } from "../format";
import { usePlayer } from "../player/PlayerContext";
import { useLoad } from "../useLoad";

export function AlbumTracks() {
  const { albumRowid = "" } = useParams();
  const navigate = useNavigate();
  const { data, error, loading } = useLoad(
    (signal) => getAlbum(Number(albumRowid), signal),
    [albumRowid],
  );
  const { playQueue, current, playing, shuffle, toggleShuffle } = usePlayer();

  const hasArt = data?.tracks.some((track) => track.has_art) ?? false;
  const artTrack = data?.tracks.find((track) => track.has_art)?.track_rowid ?? null;
  const glow = useCardMesh(artTrack, hasArt);

  // Tapping a track plays the album from there. Unplayable tracks are left out
  // of the queue rather than skipped over at playback time.
  const playable = data?.tracks.filter((track) => track.playable) ?? [];
  const from = { kind: "album" as const, label: data?.album_name ?? "" };

  const back = () =>
    window.history.state?.idx > 0 ? navigate(-1) : navigate("/library");

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
                src={withToken(`/api/albums/${data.album_rowid}/art`)}
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
                playable={track.playable}
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
