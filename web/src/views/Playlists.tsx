import { withToken } from "../token";
import { useState } from "react";
import { Link } from "react-router-dom";

import "../styles/library.css";
import "../styles/playlists.css";
import { getPlaylist, getPlaylists, type Playlist } from "../api";
import { PlayIcon } from "../components/Icons";
import { count, runtime } from "../format";
import { FeelPicker, useFeel } from "../muse/FeelPicker";
import { MakePlaylist } from "../muse/MakePlaylist";
import { usePlayer, type MoodPoint } from "../player/PlayerContext";
import { useLoad } from "../useLoad";

/** First four distinct albums' covers, as the server picked them. */
export function Mosaic({ ids }: { ids: number[] }) {
  return (
    <div className="mosaic" aria-hidden="true">
      {ids.slice(0, 4).map((id) => (
        <img key={id} src={withToken(`/api/tracks/${id}/art`)} alt="" loading="lazy" />
      ))}
    </div>
  );
}

export const playlistMeta = (p: Playlist) =>
  [count(p.track_count, "track"), runtime(p.runtime_ms)].filter(Boolean).join(" · ");

/* The feeling is asked after the button, and kept local: choosing a mood here
 * must not steer what is playing. */
function MakeSection({ onSaved }: { onSaved: () => void }) {
  const [open, setOpen] = useState(false);
  const [point, setPoint] = useState<MoodPoint | null>(null);
  const feel = useFeel("playlists.feel", point, setPoint);
  const { current } = usePlayer();
  if (!open) {
    return (
      <button type="button" className="btn primary make-open" onClick={() => setOpen(true)}>
        Make a playlist
      </button>
    );
  }
  return (
    <section className="panel-card make-panel" aria-labelledby="mkH">
      <h2 id="mkH">How are you feeling?</h2>
      <FeelPicker feel={feel} id="pl" />
      <MakePlaylist feel={feel} seed={current?.track_rowid ?? null} onSaved={onSaved} />
      <button type="button" className="link-btn" onClick={() => setOpen(false)}>Close</button>
    </section>
  );
}

export function Playlists() {
  const [attempt, setAttempt] = useState(0);
  const { data, error, loading } = useLoad((signal) => getPlaylists(signal), [attempt]);
  const { playQueue } = usePlayer();
  const [failed, setFailed] = useState<number | null>(null);

  const play = async (p: Playlist) => {
    setFailed(null);
    try {
      const detail = await getPlaylist(p.id);
      playQueue(
        detail.tracks.filter((t) => t.playable),
        0,
        { kind: "playlist", label: p.name, playlistId: p.id },
      );
    } catch {
      setFailed(p.id);
    }
  };

  return (
    <div className="view">
      <h1 className="page-title serif">Playlists</h1>
      <MakeSection onSaved={() => setAttempt((n) => n + 1)} />
      {error && <p className="lib-error" role="alert">{error}</p>}
      {!error && loading && !data && <p className="lib-empty">Loading</p>}
      {data && data.length === 0 && (
        <p className="lib-empty">
          No playlists yet. Make one on the <Link to="/muse">Muse tab</Link>.
        </p>
      )}
      {data && data.length > 0 && (
        <ul className="saved" style={{ listStyle: "none", padding: 0 }}>
          {data.map((p) => (
            <li key={p.id} className="plist">
              <Mosaic ids={p.cover_track_rowids} />
              <Link className="plist-link" to={`/playlists/${p.id}`}>
                <strong>{p.name}</strong>
                <span>{playlistMeta(p)}</span>
                {failed === p.id && <span className="error" role="alert"> Could not play</span>}
              </Link>
              <button
                type="button"
                className="btn"
                disabled={p.track_count === 0}
                aria-label={`Play ${p.name}`}
                onClick={() => play(p)}
              >
                <PlayIcon aria-hidden="true" />
                Play
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
