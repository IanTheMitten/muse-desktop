# Layout

Where things sit, and why. `PRODUCT.md` says who this is for; `DESIGN.md` says
what it looks like. This file is **information architecture and spatial order**,
and it follows `web/src/styles/shell.css`.

One user, one library. Phone in bed, one-handed, as often as a laptop at a
desk. The shell adapts to the width; the content does not change.

---

## 1. Destinations

Five, in the order the navigation shows them (`web/src/components/NavBar.tsx`):

| Route | Label |
|---|---|
| `/` | Home |
| `/library` | Library |
| `/muse` | Muse |
| `/playlists` | Playlists |
| `/add` | Add |

Add is MusicBrainz browse (artist, releases, tracklist) plus the download queue,
failures and retry; the old catalog browse is retired.

Detail screens sit under them: `/albums/:albumRowid`, `/artists/:artist`,
`/playlists/:playlistId`. Library holds the search field and the Albums /
Artists / Songs tabs; the Songs tab lists every song. `/search` redirects to
`/library`, and `/radio` and `/vibe` redirect to `/muse`.

**Now Playing is not a route you travel to.** It is a history-backed overlay:
opening it pushes a history entry, and the close button, Esc and the back
gesture all call `history.back()`. `/now` and `/lyrics` stay as routes that open
the overlay over the current (or Home) screen. Lyrics is a panel inside Now
Playing.

---

## 2. Shell by width

| Width | Nav | Player | Content |
|---|---|---|---|
| < 600 px | 5 bottom tabs, glass | floating mini-player card above the tabs, accent progress line | 2-column cover grid |
| 600-1023 px | 84 px icon rail | docked bar: art, title, play/next | grids by container width |
| >= 1024 px | 232 px labelled rail with wordmark | full transport and scrubber in the middle, lyrics and queue buttons on the right | full width, up to 1600 px |

`--rail` is 0, 84 px and 232 px at those widths. `--player-h` is 72 px, 80 px
and 88 px. `--tabs-h` is 64 px below 600 px and 0 above. Gutters are
`max(clamp(16px, 3.2vw, 48px), safe-area)`. `.view` is capped at 1600 px.

---

## 3. Breakpoints and grids

Viewport breakpoints are **600**, **900** and **1024** px:

- **600 px**: bottom tabs become the icon rail; the mini-player becomes a docked
  bar.
- **900 px**: the album page splits into two columns (cover on the left, tracks
  on the right, columns `minmax(280px, 400px) minmax(0, 1fr)`). Now Playing
  splits into two columns, with the lyrics or queue panel always visible on the
  right.
- **1024 px**: the labelled rail and the full transport.

Cover grids use **container queries**, not viewport queries. `.covers` has 2, 3,
4, 5 and then 6 columns at container widths 0, 520, 760, 1000 and 1280 px. The
`.covers.row` variant is a horizontal snap scroller, with column widths of 42%,
30%, 23%, 18.5% and 15.5% at the same container widths. The Library gallery
(`.covers.lib`) runs 3 to 8 columns, at 0, 560, 760, 900, 1060 and 1300 px.

---

## 4. Invariants

1. **Now Playing never leaves the page behind it.** The overlay sits over the
   current screen; closing returns to where the listener was, and focus returns
   to the element that opened it.
2. **Within-page state uses `replaceState`.** Library search text, the Albums /
   Artists / Songs tab and the Lyrics / Up next switch add no back-button
   stops.
3. **Tap targets are 44 px or larger.**
4. **Priority is expressed by order and width, never by colour.**
5. **Every route survives a reload with the player intact.** Layout is chrome
   around a persistent player.
