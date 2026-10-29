# Product

## Register

product

## Users

One person: the owner of the machine. Sole user, no accounts, no sharing, no
multi-tenancy. Reaches the app from a phone in bed, a MacBook at a desk, or the
server itself — always over a Tailscale tailnet, never the public internet.

The job to be done is ordinary and constant: *put on music I already own, from
whatever device is in my hand.* Secondary jobs, in order of how often they
happen: find a specific song by half-remembering its name; decide what to play
next; read the words; add something new to the library.

Context matters to the design. This is used one-handed, often in the dark, often
while doing something else. It is never a screen someone sits and studies.

## Product Purpose

Muse Player turns a 3089-track local library across 376 albums, a 17.8 GB Spotify catalog mirror,
and a pile of one-off Python scripts into one self-hosted web app on the
tailnet. It plays the library, recommends what to play next from what the audio
sounds like and what the lyrics say, shows and repairs lyrics, and grows the
library without an interactive terminal script.

Success is that the terminal stops being the interface — and that the play
history it accumulates is good enough to train the slice 2 recommender.

## Brand Personality

**The record wash.** The room takes the colour of whatever is playing. Each
cover is reduced to four colours, each scaled into a luminance band so text
stays legible by construction, and laid behind the app as a mesh of radial
gradients. Change the record and the room crossfades to the next one. Three
words: drenched, legible, the owner's own.

The scene it is designed for: *a dark room at 1am, phone in one hand, the screen
the only light in the room* — and the same app surviving a window full of
daylight the next morning. Both themes are first-class.

With no record loaded the room is plain ink: `#121117` in dark, `#f4f3f6` in
light. The housing has no hue of its own, because the colour on screen is not
ours: it is the colour of the record that is playing. The accent is the most
saturated of the cover's four colours, adjusted until it holds contrast
against the brightest wash it can sit on. A cover with no colour gets an accent
with no colour; there is no stand-in hue.

The accent marks state: the play button, progress, the current track, and the
active tab underline. Type is Figtree for the interface and Newsreader for
titles and lyrics. The one place the app is allowed to be loudest is Now
Playing, where the wash is at its brightest band.

Voice: plain and specific. "File missing", not "Oops! Something went wrong."
Nothing exclaims.

## Anti-references

- **Spotify / Apple Music marketing chrome** — promo shelves, editorial
  carousels, anything selling something. Nothing here is for sale. A row of the
  listener's own recently played albums is not a shelf; a row of things
  somebody wants them to hear is. The test is who chose the contents.
- **Neutral streaming dark mode.** `#121212` and grey-on-grey. The room is
  coloured by the record; a flat grey near-black would make this app
  indistinguishable from every other player.
- **Plex / Jellyfin / Navidrome admin density** — server dashboards, transcode
  settings, tabbed settings panes surfaced to a listener.
- **Generic Bootstrap media server** — grey cards on grey, default gaps, a
  hamburger menu that opens a tree.
- **Skeuomorphic overload.** No rendered VU gauges, swinging needles, bezels,
  or brushed-metal faceplates. The only decoration is the record's own colour.

## Design Principles

1. **The record sets the colour of the room.** The cover is the source of the
   wash and of the accent, so the interface never introduces a colour the record
   did not already contain. Contrast is solved by scaling each colour into a
   luminance band, not by a grey scrim, so the colour is not dulled to buy
   legibility. The accent signals state, never decorates.
2. **One-handed, in the dark.** Tap targets ≥44 px, light and dark both
   first-class, nothing that needs two hands or a steady surface.
3. **Show the loss, never hide it.** A track whose file is missing still appears,
   marked and untappable. A silently shorter list is worse than a visible gap.
4. **Degrade in quality, never in availability.** Art, lyrics, and recommendations
   are background work. A slow or dead external service changes how the app
   looks, never whether it plays.
5. **Spend the design budget where it means something.** *Tracklists* stay
   plain — rows, numbers, durations, nothing else. Browsing is a different job
   and gets a gallery, because choosing an album is a visual act and the covers
   are the only information that matters at that moment. The now-playing view
   and the synced-lyrics view are where craft goes, because they are the
   surfaces someone actually looks at.
6. **Motion is a readout, never atmosphere.** If something moves, it is
   reporting a fact. The wash crossfades over 0.9 s because the record
   changed. The cover on Now Playing scales to 0.92 while paused because
   playback paused. The scrubber advances because time does. Anything that moves
   to look alive is deleted.

## Accessibility & Inclusion

- WCAG 2.1 AA as the floor: body text ≥4.5:1, large text ≥3:1, in both themes.
  Measured against *every* surface a token can land on, and on the composited
  pixel where anything sits over a tint — not against the most flattering
  background available.
- `prefers-reduced-motion` is honored everywhere. Every animation and transition is
  instant, including the wash crossfade, and lyric auto-scroll jumps instead of
  scrolling smoothly.
- Every control is reachable and operable by keyboard with a visible
  `:focus-visible` ring, and every icon-only button carries an accessible name.
- Color is never the only signal: playing state, missing files, and mode toggles
  all carry a glyph or text as well as a color.
- **The accent and the error color are close in luminance and opposed in hue,**
  which is the hardest pairing for red-green color vision deficiency. They
  therefore never appear in the same control, and neither is ever the sole
  carrier of meaning. This constraint is structural, not advisory.
- Font size ≥16 px on any input, so iOS does not zoom the page on focus.
