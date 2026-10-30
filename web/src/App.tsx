import { useEffect, useRef } from "react";
import {
  BrowserRouter,
  Navigate,
  Route,
  Routes,
  useNavigate,
} from "react-router-dom";

import { ControlBar } from "./components/ControlBar";
import { DropZone } from "./components/DropZone";
import { JobProgress } from "./components/JobProgress";
import { NavBar } from "./components/NavBar";
import { SmartBanner } from "./components/SmartBanner";
import { ShellBridge } from "./components/ShellBridge";
import { JobsProvider } from "./jobs/JobsContext";
import { NowPlayingSheet } from "./player/NowPlayingSheet";
import { PlayerProvider } from "./player/PlayerContext";
import { useDownloaderUnlocked } from "./useDownloader";
import { SheetProvider, type SheetOpen } from "./player/SheetContext";
import { AlbumTracks } from "./views/AlbumTracks";
import { ArtistAlbums } from "./views/ArtistAlbums";
import { Library } from "./views/Library";
import { Add } from "./views/Add";
import { Home } from "./views/Home";
import { Muse } from "./views/Muse";
import { Settings } from "./views/Settings";
import { Playlists } from "./views/Playlists";
import { PlaylistDetail } from "./views/PlaylistDetail";
import { Wash } from "./components/Wash";
import "./styles/shell.css";
import "./styles/wash.css";
import "./styles/cards.css";
import "./styles/np.css";
import "./styles/settings.css";

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
        <ShellBridge />
        <BrowserRouter>
          <SheetProvider>
            <Wash where="app" />
            <div className="app">
              <NavBar />
              <main className="main">
                <SmartBanner />
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
                  <Route path="/add" element={<AddRoute />} />
                  <Route path="/settings" element={<Settings />} />
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
            <DropZone />
          </SheetProvider>
        </BrowserRouter>
      </PlayerProvider>
    </JobsProvider>
  );
}

/** Add exists only while unlocked; a locked or unanswered install never sees it. */
function AddRoute() {
  const unlocked = useDownloaderUnlocked();
  if (unlocked === null) return null;
  return unlocked ? <Add /> : <Navigate to="/" replace />;
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
