import { useRef, useState } from "react";
import { useParams } from "react-router-dom";

import "../styles/library.css";
import { getAlbum, getArtistAlbums, getArtists, setArtistHidden, uploadArtistImage } from "../api";
import { count, runtime } from "../format";
import { useLoad } from "../useLoad";
import { CoverArt } from "../components/CoverArt";
import { CoverCard } from "../components/CoverCard";
import { Screen } from "../components/Screen";
import { useJobs } from "../jobs/JobsContext";
import { usePlayer } from "../player/PlayerContext";
import { playableOnly } from "../player/formats";

export function ArtistAlbums() {
  const { artist = "" } = useParams();
  const { data, error, loading } = useLoad(
    (signal) => getArtistAlbums(artist, signal),
    [artist],
  );
  // has_image lives on the artist list, not the albums response.
  const { data: artists } = useLoad((signal) => getArtists(signal), []);
  const { playQueue } = usePlayer();
  const { bumpArt } = useJobs();
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploaded, setUploaded] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [fail, setFail] = useState<string | null>(null);

  const hasImage = uploaded || (artists?.find((a) => a.artist_name === artist)?.has_image ?? false);
  const firstArt = data?.albums.find((a) => a.has_art);

  const changePicture = async (file: File | undefined) => {
    if (!file) return;
    setFail(null);
    try {
      await uploadArtistImage(artist, file);
      setUploaded(true);
      bumpArt();
    } catch (cause) {
      setFail(`Couldn't use that image: ${cause instanceof Error ? cause.message : String(cause)}`);
    }
  };

  const hide = async () => {
    setFail(null);
    try {
      await setArtistHidden(artist, true);
      setStatus(`${artist} is hidden. Unhide it in Settings.`);
    } catch (cause) {
      setFail(cause instanceof Error ? cause.message : String(cause));
    }
  };

  const portrait = (
    <span className="artist-portrait-art">
      <CoverArt
        src={
          hasImage
            ? `/api/artists/${encodeURIComponent(artist)}/image`
            : firstArt
              ? `/api/albums/${firstArt.album_rowid}/art`
              : null
        }
        hasArt={hasImage || Boolean(firstArt)}
        size="hero"
        alt={`Picture of ${artist}`}
      />
    </span>
  );

  return (
    <Screen
      title={artist}
      back={{ to: "/library", label: "Library" }}
      hero={portrait}
      error={error}
      loading={loading}
    >
      <div className="media-actions">
        <button type="button" className="link-btn" onClick={() => fileRef.current?.click()}>
          Change picture
        </button>
        <input
          ref={fileRef}
          type="file"
          className="vh"
          accept="image/jpeg,image/png,image/webp"
          tabIndex={-1}
          aria-hidden="true"
          onChange={(event) => {
            void changePicture(event.target.files?.[0]);
            event.target.value = "";
          }}
        />
        <button type="button" className="link-btn" onClick={() => void hide()}>
          Hide artist
        </button>
      </div>
      {status && <p className="media-note" role="status">{status}</p>}
      {fail && <p className="media-note" role="alert">{fail}</p>}
      {data && (
        <div className="section grid-wrap">
          <div className="covers">
            {data.albums.map((album) => (
              <CoverCard
                key={album.album_rowid}
                to={`/albums/${album.album_rowid}`}
                title={album.album_name}
                subtitle={`${count(album.track_count, "track")}${
                  album.runtime_ms ? ` · ${runtime(album.runtime_ms)}` : ""
                }`}
                artSrc={album.has_art ? `/api/albums/${album.album_rowid}/art` : null}
                onPlay={() =>
                  void getAlbum(album.album_rowid).then((detail) => {
                    const tracks = playableOnly(detail.tracks);
                    if (tracks.length) playQueue(tracks, 0, { kind: "artist", label: artist });
                  })
                }
              />
            ))}
          </div>
        </div>
      )}
    </Screen>
  );
}
