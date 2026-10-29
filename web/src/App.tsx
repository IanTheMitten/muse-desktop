import { useEffect, useRef } from "react";
import {
  BrowserRouter,
  Navigate,
  Route,
  Routes,
  useNavigate,
} from "react-router-dom";

import { ControlBar } from "./components/ControlBar";
import { JobProgress } from "./components/JobProgress";
import { NavBar } from "./components/NavBar";
import { JobsProvider } from "./jobs/JobsContext";
import { NowPlayingSheet } from "./player/NowPlayingSheet";
import { PlayerProvider } from "./player/PlayerContext";
import { SheetProvider, type SheetOpen } from "./player/SheetContext";
import { AlbumTracks } from "./views/AlbumTracks";
import { ArtistAlbums } from "./views/ArtistAlbums";
import { Library } from "./views/Library";
import { Add } from "./views/Add";
import { Home } from "./views/Home";
import { Muse } from "./views/Muse";
import { Playlists } from "./views/Playlists";
import { PlaylistDetail } from "./views/PlaylistDetail";
import { Wash } from "./components/Wash";
import "./styles/shell.css";
import "./styles/wash.css";
import "./styles/cards.css";
import "./styles/np.css";

/* PlayerProvider is above BrowserRouter on purpose. The audio element it
 * renders must outlive every route change — that is the whole point. Moving it
 * inside a route, or inside Routes, silently reintroduces the bug: music stops
 * the moment the listener taps an artist.
 *
 * JobsProvider is above it for a smaller reason: one EventSource for the app,
 * not one per view. */

export function App() {
  return (
    <JobsProvider>
      <PlayerProvider>
        <BrowserRouter>
          <SheetProvider>
            <Wash where="app" />
            <div className="app">
              <NavBar />
              <main className="main">
                <Routes>
                  <Route path="/" element={<Home />} />
                  <Route path="/library" element={<Library />} />
                  <Route path="/artists/:artist" element={<ArtistAlbums />} />
                  <Route path="/albums/:albumRowid" element={<AlbumTracks />} />
                  {/* Search was a destination; it is a field inside the library
                      now. The route is kept and redirected rather than deleted, so a
                      bookmark or a back button from before the change still lands
                      somewhere that answers the same question. */}
                  <Route path="/search" element={<Navigate to="/library" replace />} />
                  <Route path="/add" element={<Add />} />
                  <Route path="/muse" element={<Muse />} />
                  <Route path="/playlists" element={<Playlists />} />
                  <Route path="/playlists/:playlistId" element={<PlaylistDetail />} />
                  <Route path="/radio" element={<Navigate to="/muse" replace />} />
                  <Route path="/vibe" element={<Navigate to="/muse" replace />} />
                  {/* Kept as routes, redirected into the overlay. A bookmark from
                      before now playing became an overlay still lands on the record. */}
                  <Route path="/now" element={<OpenOverlay panel="art" />} />
                  <Route path="/lyrics" element={<OpenOverlay panel="lyrics" />} />
                  <Route path="*" element={<Home />} />
                </Routes>
              </main>
              <ControlBar />
              <JobProgress />
            </div>
            <NowPlayingSheet />
          </SheetProvider>
        </BrowserRouter>
      </PlayerProvider>
    </JobsProvider>
  );
}

/** Landing on `/now` or `/lyrics` puts Home underneath and the overlay on top:
 *  replace first, then push, so back lands on Home rather than leaving the app. */
function OpenOverlay({ panel }: { panel: SheetOpen }) {
  const navigate = useNavigate();
  const done = useRef(false); // StrictMode runs effects twice; push once
  useEffect(() => {
    if (done.current) return;
    done.current = true;
    navigate("/", { replace: true });
    navigate("/", { state: { np: panel } });
  }, [navigate, panel]);
  return null;
}
