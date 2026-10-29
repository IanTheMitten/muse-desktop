# Design

The visual system for Muse Player, and what `web/src/styles/tokens.css`,
`web/src/styles/wash.css` and `web/src/player/wash.ts` implement.

`PRODUCT.md` says who this is for. `LAYOUT.md` says where things sit. This file
says what it is made of.

---

## 1. The scene

> A dark room at 1am, phone held in one hand, the other hand doing something
> else. The screen is the only light source in the room. At a desk the next
> morning the same app has to survive a window full of daylight.

Everything below follows from that sentence.

The app is a **record wash**: the room takes the colour of the record that is
playing, and changes with it.

### Color strategy: **Drenched by the record, contrast by construction**

A cover is downsampled to 32x32 and bucketed at 4 bits per channel. Each bucket
scores `count x (0.35 + saturation)`, and the top 4 colours at least 48 RGB
units apart become the palette. A cover that yields fewer than 4 repeats its
first colour. A cover that cannot be read falls back to a fixed grey palette.

There is no grey scrim. Each palette colour keeps its hue and is scaled into a
luminance band, so text contrast holds by construction:

| Surface (`where`) | Dark theme: max relative luminance | Light theme: min relative luminance |
|---|---|---|
| `np`, Now Playing | 0.11 | 0.36 |
| `card`, Home cards and album glow | 0.09 | 0.45 |
| `app`, the browse wash | 0.06 | 0.55 |

Dark scales a colour toward black (each step multiplies by 0.95, up to 80
steps) until it is at or under the band. Light mixes toward white (6% a step, up
to 80 steps) until it is at or over the band.

Before banding, every palette colour is pushed 1.5x away from its own grey, so
the room reads as the record. The mesh is four radial gradients at
12% 18%, 88% 12%, 82% 88% and 8% 92%, fading to transparent at 62%, 58%, 64%
and 58%, over a base of colour 1 mixed 15% toward black (dark) or white
(light).

> **The room is the record's colour. The text is always legible against it.**

---

## 2. Theme, tokens, type

Both themes are designed, neither is flipped. Dark is the base `:root`; light is
the `prefers-color-scheme: light` override, and `data-theme="dark"` or
`data-theme="light"` on the root element forces either.

| Token | Dark | Light |
|---|---|---|
| `--ink` (housing with no record) | `#121117` | `#f4f3f6` |
| `--ink-raised` | `#1c1b22` | `#ffffff` |
| `--text` | `#f5f3f0` | `#141218` |
| `--text-2` | `#dcd9d4` | `#35313b` |
| `--hairline` | `rgb(255 255 255 / 0.08)` | `rgb(20 18 24 / 0.09)` |
| `--glass` | `rgb(20 19 26 / 0.62)` | `rgb(255 255 255 / 0.66)` |
| `--accent` (no record) | `#e9e6e1` | `#2b2830` |
| `--on-accent` (no record) | `#121117` | `#ffffff` |

`--accent` and `--on-accent` are replaced per record by `applyAccent`.

### Accent rule

The accent is the most saturated of the four palette colours. If its saturation
is under 0.18 it becomes the neutral above (`#e9e6e1` dark, `#2b2830` light):
there is no house hue. It is then lightened (dark) or darkened (light), 8% a
step for up to 40 steps, until it reaches 3.2:1 against the `np` band, the worst
background it sits on. Text on the accent is `#121117` or `#ffffff`, whichever
contrasts more.

The accent marks the play button, progress, the current track, the 3-bar
equaliser glyph, and the active tab underline.

### Type

- **Figtree Variable** for the interface, **Newsreader Variable** for titles
  and lyrics (the `.serif` class, Now Playing title, lyric lines, playlist and
  album names). Both are bundled through `@fontsource-variable/*` in
  `web/src/main.tsx`, not loaded at runtime.
- `--font`: `"Figtree Variable", ui-rounded, system-ui, sans-serif`.
- `--serif`: Newsreader, then `"Noto Serif CJK KR"`, `"Noto Serif CJK JP"`,
  `"Hiragino Mincho ProN"`, `"Iowan Old Style"`, Georgia, serif. The CJK faces
  sit ahead of the generic fallbacks so Korean and Japanese titles do not drop
  to a sans face.
- Tabular figures are on globally (`font-feature-settings: "tnum" 1`).
- Scale: `--t-xs` 0.75rem, `--t-s` 0.875rem, `--t-m` 1rem, `--t-l` 1.25rem,
  `--t-xl` `clamp(1.5rem, 1.1rem + 1.6vw, 2.25rem)`, `--t-2xl`
  `clamp(2rem, 1.2rem + 3.4vw, 3.75rem)`.
- Page titles: 800 weight at -0.035em (`.page-title`); serif titles are 600.
- Inputs are 16 px or larger so iOS does not zoom.

### Shape and spacing

Radii `--r-s` 8px, `--r-m` 14px, `--r-l` 22px, `--r-pill` 999px. Easing
`--ease`: `cubic-bezier(.2,.8,.2,1)`. Gutters:
`max(clamp(16px, 3.2vw, 48px), safe-area)`.

---

## 3. Color

Colour comes from two places: the fixed tokens in §2, and the record. Nothing
else is a colour decision. There is no brand hue, no second palette, and no
status palette.

- **Surfaces.** The wash (`.wash`, fixed, `z-index: 0`, `background: var(--ink)`)
  sits behind the app. Anything that has to read as a surface on top of it is
  `--glass` with `backdrop-filter: blur(...)` and a 1px inset `--hairline` ring:
  `.nav`, `.player`, `.panel-card`, text inputs, the Add activity panel and the
  job strip. `--ink-raised` is used where a surface must be opaque (the lyric
  offset popover).
- **Text.** `--text` for names and body, `--text-2` for everything secondary
  (artists, durations, labels, captions). There is no third text step.
- **Dividers and quiet fills.** `--hairline`: row and button hover, the
  unfilled track of every slider and progress bar, placeholder art
  (`.art-tile`), the unselected `.seg` and the selected `.np-tabs` button.
- **Accent.** Per record, from §2. See §6 for where it is used.
- **Cover cards.** `useCardMesh` (`web/src/components/Wash.tsx`) gives a card
  its own mesh from its cover, in the `card` band. It is used by the Home
  resume card and picks, the album header glow and the mini player's tint. The
  Home cards lay a 50% `--ink` veil over it (`.resume-bg::after`,
  `.pick-bg::after`).

### Rules that keep this honest

**Contrast is built, not checked.** The wash is banded in luminance (§1), the
accent is pushed to 3.2:1 against the worst band (§2), and body text is
`--text` or `--text-2` on top. No component picks its own colour from the
cover.

**A record with no colour does not get a stand-in.** An unreadable cover falls
back to a fixed grey palette; a cover whose most saturated colour is under 0.18
saturation gets the neutral accent. The interface does not state a hue the
record does not have. With nothing playing there is no palette at all, the wash
layers fade out, and `--ink` and the neutral accent in `tokens.css` show.

**State never rides on colour alone.** The current track is the accent *and* an
`.eq` glyph in place of its number (or over its art). The current tab is
underlined below 1024px and gets a `--hairline` fill on the desktop rail;
`NavLink`'s `aria-current="page"` is what the stylesheet keys on. A pressed
`.seg` button inverts to `--text` on `--ink`. A missing file is dimmed
(`opacity: .55`), labelled "File missing" in the sub-line, and `aria-disabled`.

**Errors are not coloured.** There is no danger token. `.error` is `--text` at
weight 600 on a `--hairline` fill; `.lib-error` is `--text`. An error is a
sentence, not a hue.

**Banned:** a coloured left border on the selected row; grey scrims over the
wash; a house accent hue.

---

## 4. Typography

Two families, split by role, both bundled (§2): **Figtree Variable** for the
interface and **Newsreader Variable** for names that deserve a serif.

> **Newsreader for what a record is called. Figtree for everything you do to it.**

Newsreader (`--serif`, weight 600) carries titles of things: `.cover-t`,
`.row-name`, `.plist-t`, `.artist strong`, `.pick strong`, `.resume h2`,
`.np-title`, `.add-title`, `.from strong`, the Muse readout `.feel-read`, the
Home counts `.counts dd`, and every lyric line. Figtree carries artist lines,
labels, buttons, chips, inputs, tab labels, section heads and prose.

Numbers are not set apart in a second face. `font-feature-settings: "tnum" 1`
is on `body`, so every figure is tabular and a running clock does not jitter;
`.num` (scoped under `.add`) and `.job-strip-count` repeat it explicitly.

A small tracked-uppercase label (`letter-spacing: .08em`, 700, `--t-xs`,
`--text-2`) is used for `.eyebrow`, `.np-from`, `.queue-h`, `.pick small` and
`.np-volume-label`; `.add-label` does the same at `.04em` and `--t-s`. It labels
a group or a source, never decorates a section.

### Scale

The tokens are in §2 (`--t-xs` to `--t-2xl`, with `--t-xl` and `--t-2xl`
fluid). Uses that do not take a token:

| Use | Size |
|---|---|
| Cover and row titles, `.plist-t`, `.artist strong` | 1.0625rem |
| `.cover-a` | 0.9375rem |
| Lyric line | `clamp(1.5rem, 1.05rem + 1.9vw, 2.3rem)`, 600, -0.02em |
| Plain lyrics | `--t-l` |
| `.feel-read` | 1.375rem |

Weights used are 400, 500, 600, 700 and 800 (`.page-title` and `.nav-brand`).
Headings are `text-wrap: balance` at -0.02em. Page titles wrap; they are not
truncated. Track and artist names in rows truncate with an ellipsis, except in
the Home chart shelf (`.hits .row-name`), which wraps.

---

## 5. Space, shape, elevation

Radii are the four tokens in §2: `--r-s` rows and art thumbnails, `--r-m` cover
art, inputs and cards, `--r-l` panels, the Home cards and the mini player,
`--r-pill` buttons, chips and segmented controls.

Spacing is not tokenised. Gaps are literal pixel values and `clamp()`s in each
stylesheet (rows 12px, shelves `clamp(14px, 2vw, 24px)`, section margin
`clamp(28px, 4vw, 56px)`). The page gutter is `--gutter` / `--gutter-r` from §2.
`.view` caps at 1600px.

**Shell metrics** are custom properties that the breakpoints change:

| | under 600px | 600px and up | 1024px and up |
|---|---|---|---|
| `--rail` | 0 | 84px | 232px |
| `--tabs-h` | 64px | 0 | 0 |
| `--player-h` | 72px | 80px | 88px |

Under 600px the nav is a bottom tab bar and the player floats above it as a
rounded card. From 600px the nav is a left rail and the player docks along the
bottom as a bar. From 1024px the rail gets labels and the bar gets the full
transport and a volume control. Now Playing breaks at 900px (§7).

**Elevation is glass and shadow.** Panels are `--glass` over the wash with a
backdrop blur (24px on panels and the nav, 28px on the mini player). Covers and
the Now Playing art carry a soft black drop shadow (`0 6px 20px` on cards,
`0 30px 80px` on the Now Playing art); the mini player has `0 10px 30px` on the
phone and none when docked. Hairlines are 1px `--hairline`.

### z-index

Literal values, not tokens: `.wash` 0, `.app` 1, `.player` 19, `.nav`,
`.job-strip` and `.sel-bar` (the selection bar) 20, `.np` 40, `.drop` (the drop overlay) 50. Inside Now Playing, `.np-inner` is 1, the sticky tab
row is 2 and the lyric offset backdrop is 5.

---

## 6. The accent

The accent is a **signal**, not a theme. It is the one colour that follows the
record, and it is used on:

1. **Playing.** The current row's name and number (`.row-btn[aria-current]`),
   the `.eq` glyph, the mini player's progress line (`.player-line`), the
   scrubber and volume fill, the active lyric line's dot, and the gap bar
   between lyric lines.
2. **Primary actions.** `.btn.primary`, `.button-primary` (Add), the `.cover-play`
   button, and a pressed `.chip`.
3. **Position and amount.** The tab bar's active-tab underline, the `.range`
   fill, `.rank-bar`, the Home week bars (peak day at full opacity, the rest at
   0.45), the Add coverage bar and job strip fill, and the Muse mood-map dot
   and halo.
4. **Focus.** The Now Playing, cover, row, artist, pick and playlist focus rings
   are 2px `--accent`, offset 2px. The global `:focus-visible` rule (offset 3px)
   and the Add screen (offset 2px) use `--text`.

Not the accent: headings, titles, icons at rest, borders, and hover. Hover is
`--hairline` or a small lift (`.cover:hover` rises 4px, `.pick:hover` 3px).
The pressed `.seg` and the Now Playing play button are `--text` on `--ink`.

---

## 7. Now Playing

Now Playing is a history-backed overlay, not a route (see `LAYOUT.md`). The
level meter that an earlier version of this document specified is dropped. The
expressive moment is the wash itself, at its brightest band (`np`), behind a
large cover.

- Cover art scales to 0.92 while paused (`.np.paused .np-art`, 0.4 s). That is
  a readout of playback state.
- The overlay slides up over 0.38 s with `--ease`.
- The wash crossfades between records over 0.9 s: two stacked layers, opacity
  only.
- Below 900 px: one column of art, meta, scrubber and transport, with a
  Lyrics / Up next switch; a panel replaces the art while open. At 900 px and
  up: two columns, art and controls on the left and the panel on the right.
- Title is Newsreader; lyrics are Newsreader, current line at full opacity,
  upcoming lines at 0.55, past lines at 0.32.
- `prefers-reduced-motion: reduce` makes every animation and transition
  instant.

---


## 8. Motion

Short, one easing (`--ease`), transform and opacity. No bounce and no page-load
sequence.

| What | Duration |
|---|---|
| Button press (`scale(.97)`) | 0.15s |
| Chip fill, cover-play reveal, cover hover shade | 0.2-0.25s |
| Cover card lift, pick lift, job strip fill | 0.25-0.3s |
| View enters (`.view`, `rise`: 6px up and fade in) | 0.32s |
| Now Playing slides up and down | 0.38s |
| Now Playing art scales to 0.92 when paused | 0.4s |
| Wash crossfade between records | 0.9s |
| Lyric line opacity | 0.3s |

The `.eq` glyph animates its three bars on a 1s loop and pauses when the row is
paused (`.paused .eq i`). The scrubber and the progress line advance
continuously because they report a continuous fact.

`prefers-reduced-motion: reduce` turns every animation off and every transition
to 0s (`base.css`), and the wash crossfade, Now Playing slide, cover lift,
pick lift and chip fill each carry their own reduced rule as well. Nothing is
hidden behind a transition: the default state is the visible one.

---

## 9. Components

The components that exist, and what each one does.

- **Buttons.** `.btn` (48px, pill, `--hairline` fill) with `.btn.primary` in the
  accent; `.icon-btn` (44x44, transparent, `--hairline` on hover, a faint fill
  when `aria-pressed`); Add has its own `.button`, `.button-primary` and
  `.button-quiet`. Disabled is `opacity: .45`.
- **Track rows** (`TrackRow`, `.row-btn`). Variants `plain`, `song`, `queue` and
  `chart` pick the grid: number, art, name and sub, duration. The current row is
  `aria-current="true"` with the accent name and the `.eq` glyph; a row for a
  missing file is `aria-disabled` at 0.55 opacity with "File missing" as its
  sub-line and no click handler. Hover is `--hairline`. Song rows carry a More
  menu button (`TrackMenu`) and, in a selection, a selected ring; queue rows in
  Now Playing carry a drag grip and a remove button.
- **Cover cards** (`CoverCard`, `.cover`). One link for art and names, with the
  play button floating over the art's corner: hover-revealed with a pointer,
  always visible under 600px, also shown on `:focus-within`.
- **Cover art** (`CoverArt`). A cover, or a `--hairline` tile when there is
  none. There is no broken-image state: a missing cover is ordinary.
- **Wash** (`Wash`, `useCardMesh`). See §1.
- **Navigation** (`NavBar`). Five tabs: Home, Library, Muse, Playlists, Add.
  Every tab keeps a visible label.
- **Mini player** (`ControlBar`) and **Now Playing** (`NowPlayingSheet`, §7).
- **Lyrics** (`SyncedLyrics`). Synced lines with an offset popover
  (`role="dialog"` on `--ink-raised`).
- **Mini window** (`MiniWindow`, `/mini`, desktop shell only). A 400x96
  always-on-top window with no decorations: the record's card wash over `--ink`,
  a 64px cover that brings the main window back, title and artist on one line
  each, previous, play/pause and next, and a close button. It is a remote: it
  mirrors the main window and holds no audio. Not the Mini player above.
- **Drop overlay** (`DropZone`, desktop shell only). While files are dragged
  over the window, a full-window `--glass` blur with an inset accent outline and
  "Drop to add to your library". After the drop a small `--glass` toast above the
  player says what was added (for example "Added 1 folder and 2 songs"), then
  fades after 2.5 s.
- **Boot page** (`src-tauri/boot/index.html`). The shell's first screen, before
  the UI loads: the asterisk mark in the brand gradient above "Starting Muse" on
  `--bg`, light or dark by system setting. Dark is `#24142c` (cards `#2f1c38`),
  light is `#f4f3f6`. If the server fails to start it shows "Muse couldn't
  start", the log path and the last log lines in a monospace block.
- **App icon and tray glyph** (`src-tauri/icons/icon.svg`). Phosphor's six-arm
  asterisk filled with a linear gradient from `#f5b9dc` (top left) to `#ffdb8c`
  (bottom right) on a `#28172f` rounded tile with one soft `#7a4b8f` radial
  glow, the same starburst as the project website in small. The macOS tray
  glyph (`tray-template.svg`) is the same asterisk in black, a template image;
  other platforms use the app icon. These colours are for brand surfaces only
  (icon, tray, favicon, wordmark, boot page); the in-app theme stays
  record-drenched (§1).
- **Nav wordmark** (`NavBar`, `.nav-brand`, `BrandMark`). In the wide nav rail
  (1024px and up) the lowercase word "muse" in weight 750 with letter-spacing
  `-0.04em`, followed by the asterisk at 0.8em: `#f5b9dc` in dark, `#a8437a`
  in light.
- **Smart features** (`SmartBanner`, `SmartFeatures` in Settings). The banner is
  one `--glass` line above the page: "Smart features aren't installed" with what
  they do, the download size, a primary Install and a quiet Not now (until the
  next launch). While installing, an accent fill grows behind the text and a
  percent replaces the buttons; a failure offers Try again. It is never a
  modal, and is hidden on Settings. The Settings section is a titled block
  with one status row (Installed, Not installed with its size, Installing with
  progress, or "Not available on this computer") and a small licence note.
- **Job strip** (`JobProgress`). A fixed strip above the player showing cover
  art fetch progress ("Fetching cover art · n of m") and, briefly, the result.
- **Week bars** (`WeekBars`). Seven bars, one series, no legend; an empty week
  says so in a sentence.
- **Muse** (`FeelPicker`, `MakePlaylist`). Quick-mood chips, a draggable
  valence/arousal map with the six emotion labels at their coordinates, and
  fine-tune range sliders behind a disclosure button.
- **Loading is quiet.** `Screen` and the list views show a plain "Loading"
  line only when there is nothing to show yet; there are no spinners or
  skeletons, because a spinner that flashes for 40 ms over a tailnet is worse
  than nothing. Errors are the loud state.
- **Modals are rare.** The queue and lyrics are a panel inside Now Playing. The
  lyric offset popover, Now Playing itself and four edit dialogs (Edit details,
  Remove, Add to playlist, Edit lyrics; native `<dialog>`) are the only
  `role="dialog"`s.
- Tap targets are 44px or larger; inputs are 16px or larger so iOS does not zoom.

---

## 10. What this is not

- **Not a branded dark theme.** There is no house colour. The room takes the
  record's, and with no record it is a neutral near-black or near-white.
- **Not a grey scrim over a photo.** The wash is the cover's own hues, scaled
  into a luminance band (§1).
- **Not skeuomorphic.** No VU meter, no spinning record, no needle, no bezel.
- **Not a dashboard of coloured status.** One accent, from the record, and no
  green/amber/red.
- **No decorative motion.** If it moves and is not reporting something, delete it.

---

## 11. Retired

Three systems came before this one and are gone from `web/src`: the green-black
"chassis" with the `--acid` accent and its `--ink-muted`, `--edge` and `--danger`
tokens; the level meter and the spinning record (`--revolution`); and the
per-cover page tint (`useArtTint`). A grep for `chassis`, `--acid`,
`--danger`, `AnalyserNode`, `attachMeter`, `readLevels` and `useArtTint` in
`web/src` returns nothing. The cover-card mesh (`useCardMesh`) is the same
wash engine as the app background, not the old tint.

`.coverage-meter` on the Add screen is a 56x4px progress bar for how much of a
release is already in the library. It is unrelated to the retired level meter.

---

## 12. The mood bands

The server's dashboard payload still describes the library in four mood bands
(`BandKey` in `web/src/api.ts`: `bright`, `warm`, `low`, `intense`), and the
client still exposes `getMix(band)`. No screen renders them today: there is no
band token, library mood plot, share bar or legend in `web/src`.

The bands are quadrants of a plane, `valence` across and `energy` up, both
split at 0.5. `BAND_ORDER` is the reading order on the map:

```
              energy ↑
      intense   │   bright
      (v−, e+)  │   (v+, e+)
     ───────────┼───────────  valence →
        low     │    warm
      (v−, e−)  │   (v+, e−)
```

If a surface for them returns, two rules from the previous design still stand.
The four bands are two-dimensional, so a sequential colour ramp is the wrong
encoding; and the band called **warm** describes how a track sounds, never the
palette, and nothing in the UI should imply it does.

The Muse screen's mood map (`FeelPicker`) is a different thing: a user-steered
point on a valence/arousal plane drawn in `--accent`, not the library's bands.
