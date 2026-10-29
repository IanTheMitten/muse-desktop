import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import {
  getAlbum,
  getAlbums,
  getArtists,
  getSongs,
  searchLibrary,
  type Artist,
  type LibraryAlbum,
  type Track,
} from "../api";
import "../styles/library.css";
import { CoverArt } from "../components/CoverArt";
import { CoverCard } from "../components/CoverCard";
import { Screen } from "../components/Screen";
import { TrackRow } from "../components/TrackRow";
import { playableOnly, unplayableReason } from "../player/formats";
import { count } from "../format";
import { useJobs } from "../jobs/JobsContext";
import { usePlayer } from "../player/PlayerContext";

/* Browse and search are one place. The whole library is fetched once and
 * filtered here; track search still goes to the server, because it is an FTS
 * query with fuzzy fallbacks a substring match could not reproduce. Tab and
 * query live in the URL (replace, so typing does not stack history entries). */

type Segment = "albums" | "artists" | "songs";

const SEGMENTS: { key: Segment; label: string }[] = [
  { key: "albums", label: "Albums" },
  { key: "artists", label: "Artists" },
  { key: "songs", label: "Songs" },
];

/** Case- and accent-insensitive contains. `localeCompare` is the correct tool
 *  for ordering and the wrong one for this; normalising away the diacritics is
 *  what lets "beabadoobee" match a query typed without them. */
function matches(haystack: string, needle: string): boolean {
  return haystack
    .normalize("NFD")
    .replace(/\p{Diacritic}/gu, "")
    .toLowerCase()
    .includes(needle);
}

export function Library() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab");
  const segment: Segment = tab === "artists" || tab === "songs" ? tab : "albums";
  const query = params.get("q") ?? "";
  const [artists, setArtists] = useState<Artist[] | null>(null);
  const [albums, setAlbums] = useState<LibraryAlbum[] | null>(null);
  const [songs, setSongs] = useState<Track[]>([]);
  const [allSongs, setAllSongs] = useState<Track[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { playQueue, current, playing } = usePlayer();
  const { libraryVersion } = useJobs();

  const update = (next: { tab?: Segment; q?: string }) => {
    const out = new URLSearchParams(params);
    if (next.tab !== undefined) out.set("tab", next.tab);
    if (next.q !== undefined) {
      if (next.q) out.set("q", next.q);
      else out.delete("q");
    }
    setParams(out, { replace: true });
  };

  // Both lists on open, and again when a scan ends. The song list is dropped
  // so the Songs tab refetches it too.
  useEffect(() => {
    if (libraryVersion > 0) setAllSongs(null);
    const abort = new AbortController();
    Promise.all([getArtists(abort.signal), getAlbums(abort.signal)])
      .then(([loadedArtists, loadedAlbums]) => {
        if (abort.signal.aborted) return;
        setArtists(loadedArtists);
        setAlbums(loadedAlbums);
      })
      .catch((cause: unknown) => {
        if (!abort.signal.aborted) {
          setError(cause instanceof Error ? cause.message : String(cause));
        }
      });
    return () => abort.abort();
  }, [libraryVersion]);

  // Every song, once, the first time the Songs tab opens.
  useEffect(() => {
    if (segment !== "songs" || allSongs) return;
    const abort = new AbortController();
    getSongs(abort.signal)
      .then(setAllSongs)
      .catch((cause: unknown) => {
        if (!abort.signal.aborted) {
          setError(cause instanceof Error ? cause.message : String(cause));
        }
      });
    return () => abort.abort();
  }, [segment, allSongs]);

  useEffect(() => {
    if (segment !== "songs" || !query.trim()) {
      setSongs([]);
      return;
    }
    const abort = new AbortController();
    searchLibrary(query, abort.signal)
      .then((response) => setSongs(response.results))
      .catch((cause: unknown) => {
        if (!abort.signal.aborted) {
          setError(cause instanceof Error ? cause.message : String(cause));
        }
      });
    return () => abort.abort();
  }, [segment, query]);

  const needle = query.trim().normalize("NFD")
    .replace(/\p{Diacritic}/gu, "").toLowerCase();

  const shownArtists = useMemo(
    () =>
      !artists
        ? []
        : needle
          ? artists.filter((artist) => matches(artist.artist_name, needle))
          : artists,
    [artists, needle],
  );

  const shownAlbums = useMemo(
    () =>
      !albums
        ? []
        : needle
          ? albums.filter(
              (album) =>
                matches(album.album_name, needle) ||
                matches(album.artist_name, needle),
            )
          : albums,
    [albums, needle],
  );

  const shownSongs = query.trim() ? songs : (allSongs ?? []);
  const playable = playableOnly(shownSongs);

  const playAlbum = (album: LibraryAlbum) =>
    getAlbum(album.album_rowid)
      .then((detail) => {
        const tracks = playableOnly(detail.tracks);
        if (tracks.length) playQueue(tracks, 0, { kind: "album", label: album.album_name });
      })
      .catch((cause: unknown) =>
        setError(cause instanceof Error ? cause.message : String(cause)),
      );

  const subtitle = artists && albums
    ? needle
      ? segment === "artists"
        ? count(shownArtists.length, "artist")
        : segment === "albums"
          ? count(shownAlbums.length, "album")
          : count(songs.length, "song")
      : segment === "songs" && allSongs
        ? count(allSongs.length, "song")
        : `${count(artists.reduce((n, a) => n + a.track_count, 0), "track")} · ` +
        `${count(albums.length, "album")} · ${count(artists.length, "artist")}`
    : undefined;

  const nothing = (
    <p className="lib-empty">
      Nothing in the library matches "{query}". Try fewer words.
    </p>
  );

  return (
    <Screen
      title="Library"
      subtitle={subtitle}
      error={error}
      loading={!artists || !albums}
    >
      <div className="toolbar">
        <div className="field">
          <label htmlFor="lib-search">Search the library</label>
          <input
            id="lib-search"
            type="search"
            value={query}
            onChange={(event) => update({ q: event.target.value })}
            autoComplete="off"
            autoCapitalize="off"
            autoCorrect="off"
            spellCheck={false}
          />
        </div>
        <div className="seg" role="group" aria-label="Show">
          {SEGMENTS.map((option) => (
            <button
              key={option.key}
              type="button"
              aria-pressed={segment === option.key}
              onClick={() => update({ tab: option.key })}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>

      <div className="section grid-wrap">
        {segment === "albums" &&
          (shownAlbums.length ? (
            <div className="covers lib">
              {shownAlbums.map((album) => (
                <CoverCard
                  key={album.album_rowid}
                  to={`/albums/${album.album_rowid}`}
                  title={album.album_name}
                  subtitle={album.artist_name}
                  artSrc={album.has_art ? `/api/albums/${album.album_rowid}/art` : null}
                  onPlay={() => void playAlbum(album)}
                />
              ))}
            </div>
          ) : (
            artists && nothing
          ))}

        {segment === "artists" &&
          (shownArtists.length ? (
            <ul className="artists">
              {shownArtists.map((artist) => (
                <li key={artist.artist_name}>
                  <Link
                    className="artist"
                    to={`/artists/${encodeURIComponent(artist.artist_name)}`}
                  >
                    <span className="artist-art">
                      <CoverArt
                        src={
                          artist.art_album_rowid === null
                            ? null
                            : `/api/albums/${artist.art_album_rowid}/art`
                        }
                        hasArt={artist.art_album_rowid !== null}
                        size="thumb"
                      />
                    </span>
                    <span>
                      <strong>{artist.artist_name}</strong>
                      <span>{count(artist.album_count, "album")}</span>
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          ) : (
            artists && nothing
          ))}

        {segment === "songs" &&
          (shownSongs.length ? (
            <ul className="tracks song-list">
              {shownSongs.map((track) => (
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
                      { kind: "track", label: query.trim() ? `Search for "${query.trim()}"` : "Songs" },
                    )
                  }
                />
              ))}
            </ul>
          ) : (
            !error && (query.trim() ? nothing : allSongs && nothing)
          ))}
      </div>
    </Screen>
  );
}
