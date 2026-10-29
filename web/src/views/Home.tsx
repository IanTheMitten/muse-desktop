import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";

import {
  getAlbum,
  getChart,
  getDashboard,
  getPlaylists,
  type ChartSong,
  type LibraryAlbum,
  type Playlist,
  type RecommendedAlbum,
  type Track,
} from "../api";
import { CoverArt } from "../components/CoverArt";
import { CoverCard } from "../components/CoverCard";
import { PlayIcon } from "../components/Icons";
import { Screen } from "../components/Screen";
import { TrackRow } from "../components/TrackRow";
import { unplayableReason } from "../player/formats";
import { WeekBars } from "../components/WeekBars";
import { useCardMesh } from "../components/Wash";
import { count } from "../format";
import { usePlayer, type PlayingFrom } from "../player/PlayerContext";
import { meshCss, onThemeChange, paletteFor, type Palette } from "../player/wash";
import { useLoad } from "../useLoad";
import "../styles/home.css";

/* Home answers "what now?": carry on, choose, look back. Resumption is the
 * likeliest intent on open, so it is first; the stats nobody opens a player
 * for are last. Every shelf is one row, and an empty shelf is not drawn.
 * A chart or playlists failure drops only its own shelf. */

const trackArt = (t: Track) => (t.has_art ? `/api/tracks/${t.track_rowid}/art` : null);
const addQuery = (artist: string, name: string) =>
  `/add?q=${encodeURIComponent(`${artist} ${name}`)}`;

export function Home() {
  const [attempt, setAttempt] = useState(0);
  const { data, error, loading } = useLoad(
    async (signal) => {
      const [dash, chart, playlists] = await Promise.all([
        getDashboard(signal),
        getChart(signal).then((r) => r.songs, () => [] as ChartSong[]),
        getPlaylists(signal).catch(() => [] as Playlist[]),
      ]);
      return { dash, chart, playlists };
    },
    [attempt],
  );
  const { playQueue, current, queue, playing, toggle } = usePlayer();

  const from = (label: string): PlayingFrom => ({ kind: "home", label });
  const playTracks = (tracks: Track[], at: number, label: string) =>
    playQueue(tracks, at, from(label));
  const playAlbum = async (album: LibraryAlbum, label: string) => {
    const detail = await getAlbum(album.album_rowid);
    const tracks = detail.tracks.filter((t) => t.playable);
    if (tracks.length > 0) playQueue(tracks, 0, from(label));
  };

  if (error) {
    return (
      <Screen title={greeting()}>
        <div className="home-error">
          <p className="error" role="alert">{error}</p>
          <button type="button" className="btn" onClick={() => setAttempt((n) => n + 1)}>
            Retry
          </button>
        </div>
      </Screen>
    );
  }

  const dash = data?.dash;
  const stats = dash?.stats;
  // The restored queue's track, else the dashboard's resume track.
  const resumeTrack = current ?? dash?.resume?.track ?? null;

  const chart = data?.chart ?? [];
  const chartTracks = chart.flatMap((s) => (s.track?.playable ? [s.track] : []));
  const albumShelf = (label: string, albums: LibraryAlbum[]) => (
    <Shelf title={label} show={albums.length > 0}>
      <div className="covers row">
        {albums.map((a) => (
          <CoverCard
            key={a.album_rowid}
            to={`/albums/${a.album_rowid}`}
            title={a.album_name}
            subtitle={a.artist_name}
            artSrc={a.has_art ? `/api/albums/${a.album_rowid}/art` : null}
            onPlay={() => void playAlbum(a, label)}
          />
        ))}
      </div>
    </Shelf>
  );

  return (
    <Screen
      title={greeting()}
      subtitle={
        stats &&
        `${count(stats.library.playable, "track")} · ${hours(stats.library.runtime_ms)} · ` +
          `${count(stats.listening.plays, "play")} recorded`
      }
      loading={loading}
    >
      {dash && stats && (
        <>
          {resumeTrack && (
            <Resume
              track={resumeTrack}
              playing={playing}
              onResume={() =>
                queue.length > 0 ? toggle() : playTracks([resumeTrack], 0, "Resume")
              }
              onPause={toggle}
            />
          )}

          {albumShelf("Recents", dash.recent_albums)}

          <Shelf title="On repeat" show={dash.on_repeat.length > 0} wrap={false}>
            <ol className="hits">
              {dash.on_repeat.map((t, i) => (
                <TrackRow
                  key={t.track_rowid}
                  n={i + 1}
                  variant="chart"
                  title={t.track_name}
                  sub={`${t.artist_name} · ${count(t.plays, "play")}`}
                  durationMs={t.duration_ms}
                  artSrc={trackArt(t)}
                  current={current?.track_rowid === t.track_rowid}
                  paused={!playing}
                  unplayable={unplayableReason(t)}
                  onPlay={() => playTracks(dash.on_repeat, i, "On repeat")}
                />
              ))}
            </ol>
          </Shelf>

          <Shelf title="Top songs right now" show={chart.length > 0} wrap={false}>
            <ol className="hits">
              {chart.map((s) =>
                s.track ? (
                  <TrackRow
                    key={s.rank}
                    n={s.rank}
                    variant="chart"
                    title={s.track.track_name}
                    sub={s.track.artist_name}
                    durationMs={s.track.duration_ms}
                    artSrc={trackArt(s.track)}
                    current={current?.track_rowid === s.track.track_rowid}
                    paused={!playing}
                    unplayable={unplayableReason(s.track)}
                    onPlay={() =>
                      playTracks(chartTracks, chartTracks.indexOf(s.track!), "Top songs right now")
                    }
                  />
                ) : (
                  <li key={s.rank}>
                    <Link className="row-btn row-chart" to={addQuery(s.artist_name, s.name)}>
                      <span className="row-no">{s.rank}</span>
                      <span className="row-ph" aria-hidden="true">{s.rank}</span>
                      <span className="row-name">
                        {s.name}
                        <span className="row-sub">{s.artist_name} · Not in your library</span>
                      </span>
                      <span className="row-dur" />
                    </Link>
                  </li>
                ),
              )}
            </ol>
          </Shelf>

          {dash.for_fans_of && (
            <Shelf title={`For fans of ${dash.for_fans_of.artist_name}`} show={dash.for_fans_of.albums.length > 0}>
              <div className="covers row">
                {dash.for_fans_of.albums.map((a) => (
                  <CoverCard
                    key={a.album_rowid}
                    to={`/albums/${a.album_rowid}`}
                    title={a.album_name}
                    subtitle={a.artist_name}
                    artSrc={a.has_art ? `/api/albums/${a.album_rowid}/art` : null}
                    onPlay={() => void playAlbum(a, `For fans of ${dash.for_fans_of!.artist_name}`)}
                  />
                ))}
              </div>
            </Shelf>
          )}

          <Shelf title="Recommended albums" show={dash.recommended_albums.length > 0} wrap={false}>
            <div className="picks">
              {dash.recommended_albums.map((r) => (
                <Pick key={r.owned ? r.album_rowid : `${r.artist_name}/${r.album_name}`} pick={r} />
              ))}
            </div>
          </Shelf>

          <Shelf title="Your playlists" show={(data?.playlists.length ?? 0) > 0} wrap={false}>
            <div className="plists">
              {data?.playlists.map((p) => (
                <Link className="plist" key={p.id} to={`/playlists/${p.id}`}>
                  <span className="mosaic">
                    {p.cover_track_rowids.slice(0, 4).map((id) => (
                      <CoverArt key={id} src={`/api/tracks/${id}/art`} hasArt size="tile" />
                    ))}
                  </span>
                  <span className="plist-t">{p.name}</span>
                  <span className="plist-n">{count(p.track_count, "track")}</span>
                </Link>
              ))}
            </div>
          </Shelf>

          {dash.sounds_like.seed && (
            <Shelf
              title={`Sounds like ${dash.sounds_like.seed.track_name}`}
              show={dash.sounds_like.tracks.length > 0}
              wrap={false}
            >
              <ul className="hits">
                {dash.sounds_like.tracks.map((t, i) => (
                  <TrackRow
                    key={t.track_rowid}
                    variant="chart"
                    title={t.track_name}
                    sub={t.artist_name}
                    durationMs={t.duration_ms}
                    artSrc={trackArt(t)}
                    current={current?.track_rowid === t.track_rowid}
                    paused={!playing}
                    unplayable={unplayableReason(t)}
                    onPlay={() => playTracks(dash.sounds_like.tracks, i, `Sounds like ${dash.sounds_like.seed!.track_name}`)}
                  />
                ))}
              </ul>
            </Shelf>
          )}

          {albumShelf("Never played", dash.never_played)}

          <Shelf title="Forgotten" show={dash.forgotten.length > 0} wrap={false}>
            <ul className="hits">
              {dash.forgotten.map((t, i) => (
                <TrackRow
                  key={t.track_rowid}
                  variant="chart"
                  title={t.track_name}
                  sub={t.artist_name}
                  durationMs={t.duration_ms}
                  artSrc={trackArt(t)}
                  current={current?.track_rowid === t.track_rowid}
                  paused={!playing}
                  unplayable={unplayableReason(t)}
                  onPlay={() => playTracks(dash.forgotten, i, "Forgotten")}
                />
              ))}
            </ul>
          </Shelf>

          {albumShelf("Recently added", dash.recently_added)}

          <Shelf title="Listening stats" show wrap={false}>
            <div className="stats">
              <div>
                <h3>This week</h3>
                <WeekBars week={stats.week} />
              </div>
              {stats.top_artists && stats.top_artists.length > 0 && (
                <div>
                  <h3>Top artists</h3>
                  <div className="ranks">
                    {stats.top_artists.map((a) => (
                      <Link
                        className="rank"
                        key={a.artist_name}
                        to={`/artists/${encodeURIComponent(a.artist_name)}`}
                      >
                        <span className="rank-name">{a.artist_name}</span>
                        <span className="rank-n">{count(a.plays, "play")}</span>
                        <span className="rank-bar">
                          <i style={{ width: `${Math.round((a.plays / stats.top_artists![0].plays) * 100)}%` }} />
                        </span>
                      </Link>
                    ))}
                  </div>
                </div>
              )}
              <div>
                <h3>Library</h3>
                <dl className="counts">
                  <div><dt>Tracks</dt><dd>{stats.library.playable}</dd></div>
                  <div><dt>Albums</dt><dd>{stats.library.albums}</dd></div>
                  <div><dt>Artists</dt><dd>{stats.library.artists}</dd></div>
                  <div><dt>Plays</dt><dd>{stats.listening.plays}</dd></div>
                </dl>
              </div>
            </div>
          </Shelf>
        </>
      )}
    </Screen>
  );
}

/** A named shelf; hidden whole when it has nothing to show. */
function Shelf({
  title,
  show,
  wrap = true,
  children,
}: {
  title: string;
  show: boolean;
  /** Cover rows size by their container. */
  wrap?: boolean;
  children: ReactNode;
}) {
  if (!show) return null;
  return (
    <section className={wrap ? "section grid-wrap" : "section"}>
      <div className="section-head"><h2>{title}</h2></div>
      {children}
    </section>
  );
}

function Resume({
  track,
  playing,
  onResume,
  onPause,
}: {
  track: Track;
  playing: boolean;
  onResume: () => void;
  onPause: () => void;
}) {
  const mesh = useCardMesh(track.track_rowid, track.has_art);
  return (
    <section className="section" aria-label="Resume">
      <div className="resume">
        <div className="resume-bg" style={{ background: mesh }} aria-hidden="true" />
        <span className="resume-art">
          <CoverArt src={trackArt(track)} hasArt={track.has_art} size="tile" />
        </span>
        <div>
          <div className="eyebrow">{playing ? "Playing now" : "Pick up where you left off"}</div>
          <h2>{track.track_name}</h2>
          <p>{track.artist_name} · {track.album_name}</p>
          <div className="resume-acts">
            {playing ? (
              <button type="button" className="btn primary" aria-label="Pause" onClick={onPause}>
                <span className="eq" aria-hidden="true"><i /><i /><i /></span>
                Playing
              </button>
            ) : (
              <button type="button" className="btn primary" onClick={onResume}>
                <PlayIcon aria-hidden="true" />
                Resume
              </button>
            )}
            {track.album_rowid !== null && (
              <Link className="btn" to={`/albums/${track.album_rowid}`}>Open album</Link>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}

/** A recommended album, washed in its own cover. Not-owned ones go to Add. */
function Pick({ pick }: { pick: RecommendedAlbum }) {
  const [pal, setPal] = useState<Palette | null>(null);
  const [, setTick] = useState(0);
  const id = pick.owned ? pick.album_rowid : null;
  const hasArt = pick.owned && pick.has_art;
  useEffect(() => onThemeChange(() => setTick((t) => t + 1)), []);
  useEffect(() => {
    if (id === null || !hasArt) return;
    let live = true;
    void paletteFor(id, `/api/albums/${id}/art`).then((p) => live && setPal(p));
    return () => {
      live = false;
    };
  }, [id, hasArt]);

  const body = (
    <>
      <span className="pick-bg" style={{ background: pal ? meshCss(pal.colors, "card") : undefined }} aria-hidden="true" />
      <span className="pick-art">
        <CoverArt src={id !== null ? `/api/albums/${id}/art` : null} hasArt={hasArt} size="tile" />
      </span>
      <span style={{ minWidth: 0 }}>
        <small>{pick.owned ? pick.reason : "Not in your library"}</small>
        <strong>{pick.album_name}</strong>
        <span className="pick-sub">{pick.artist_name}{pick.owned ? "" : ` · ${pick.reason}`}</span>
      </span>
    </>
  );
  return pick.owned ? (
    <Link className="pick" to={`/albums/${pick.album_rowid}`}>{body}</Link>
  ) : (
    <Link className="pick" to={addQuery(pick.artist_name, pick.album_name)}>{body}</Link>
  );
}

/** Local time, not the server's: the greeting is about the room the phone is in. */
function greeting(): string {
  const hour = new Date().getHours();
  if (hour < 5) return "Still up";
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

function hours(ms: number): string {
  return `${(ms / 3_600_000).toFixed(1)} hours`;
}
