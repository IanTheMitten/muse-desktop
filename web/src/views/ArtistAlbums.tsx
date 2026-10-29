import { useParams } from "react-router-dom";

import "../styles/library.css";
import { getAlbum, getArtistAlbums } from "../api";
import { count, runtime } from "../format";
import { useLoad } from "../useLoad";
import { CoverCard } from "../components/CoverCard";
import { Screen } from "../components/Screen";
import { usePlayer } from "../player/PlayerContext";

export function ArtistAlbums() {
  const { artist = "" } = useParams();
  const { data, error, loading } = useLoad(
    (signal) => getArtistAlbums(artist, signal),
    [artist],
  );
  const { playQueue } = usePlayer();

  return (
    <Screen
      title={artist}
      back={{ to: "/library", label: "Library" }}
      error={error}
      loading={loading}
    >
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
                    const tracks = detail.tracks.filter((t) => t.playable);
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
