import { NavLink } from "react-router-dom";

import { AddGlyph, HomeGlyph, LibraryGlyph, MuseIcon, PlaylistsIcon } from "./Icons";

/* Bottom tabs on a phone, an icon rail from 600px, a labelled rail from 1024px.
 * One element; shell.css moves it. Every tab keeps its visible label: an
 * icon-only bar is a puzzle. NavLink sets aria-current="page", which is also
 * what the stylesheet keys the active state on, so it never depends on hue. */

const TABS = [
  { to: "/", label: "Home", end: true, icon: <HomeGlyph /> },
  { to: "/library", label: "Library", end: false, icon: <LibraryGlyph /> },
  { to: "/muse", label: "Muse", end: false, icon: <MuseIcon /> },
  { to: "/playlists", label: "Playlists", end: false, icon: <PlaylistsIcon /> },
  { to: "/add", label: "Add", end: false, icon: <AddGlyph /> },
];

export function NavBar() {
  return (
    <nav className="nav" aria-label="Sections">
      <span className="nav-brand">Muse</span>
      {TABS.map((tab) => (
        <NavLink key={tab.to} to={tab.to} end={tab.end} className="tab">
          {tab.icon}
          <span className="tab-label">{tab.label}</span>
        </NavLink>
      ))}
    </nav>
  );
}
