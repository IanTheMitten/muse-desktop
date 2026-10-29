# Muse

A local music player and recommender over the music folders you point it at
and your own listening history. See
`docs/specs/2026-10-29-desktop-app-fork.md` for the desktop spec.

## Language

**Data dir**:
The one directory (`MUSE_DATA_DIR`, default `./data`) under which Muse keeps
everything it writes: `library.sqlite3`, `lyrics/`, `art/`, `downloads/`,
`settings.json`. Each path can be overridden singly. Music folders are not in
it; they stay where the user keeps them and are never written.
_Avoid_: Library (that is the tracks table, not the directory).

**Shell**:
The Tauri app in `src-tauri/` that gives Muse a native window, tray and mini
window. It starts the sidecar and loads the UI from it over loopback; the
library, playback state and audio stay in the sidecar and the main window's
webview.
_Avoid_: Wrapper, client (the UI is served by the sidecar, not by the shell).

**Sidecar**:
The frozen Python server (`muse-core`, built by `scripts/freeze_sidecar.py`)
that the shell spawns: `python -m muse` with the web UI bundled in.
_Avoid_: Backend, worker (the ML worker is an M4 thing).

**Parent watch**:
With `MUSE_PARENT_WATCH=1`, the sidecar reads stdin on a daemon thread
(`muse/__main__.py`); at EOF, meaning the shell exited or crashed, it asks
uvicorn to stop and, 10 s later, calls `os._exit(0)`. It is how the sidecar
dies with the shell without the shell killing it.

**Mini window**:
The always-on-top 400x96 window at `/mini` (`web/src/views/MiniWindow.tsx`),
opened from the tray. A remote: it mirrors `muse:now`, sends `muse:command`,
and has no audio element or player context. Its user-facing label is "Mini
player".
_Avoid_: Mini player in code and docs (that is `ControlBar`, DESIGN.md §9).

**Dropped song** (`music_files`):
An audio file dropped onto the main window, recorded in the `music_files` list
in `settings.json` through `POST /api/library/import` and read in place. It is
rescanned on every chain run and never watched. A dropped folder instead becomes
a watched music folder. A file already inside a music folder or already listed
counts as `already`.
_Avoid_: Imported song (nothing is copied).

**Music folder**:
A directory the user adds in settings (`PUT /api/settings/music_folders`) to be
scanned and watched. Stored in `settings.json`. Must be absolute, must exist,
and may not sit inside another music folder. Read-only to the app.
_Avoid_: Library root (`Config.music_root` is the `downloads/` dir Add writes to).

**Scan job**:
The `jobs.kind = 'scan'` run (`muse/scanner.py`, started by `run_scan_job`)
that walks every music folder, reads tags with mutagen, and upserts tracks. One
at a time: it is skipped if a scan is already active. Triggered by launch, by
the folder watcher, and by `POST /api/scan`.

**Tombstone (`status='removed'`, `removed_at`)**:
A track the user removed on purpose: `status = 'removed'` plus a `removed_at`
timestamp. The scanner skips a row with `removed_at` set even if its file is
still on disk, and status-filtered readers drop it, so a rescan does not bring
it back. The row is kept so plays and ratings survive. Restore (Settings >
Removed songs) sets `status = 'missing_file'` and clears `removed_at`; the next
scan flips it to `done` if the file exists. Distinct from `status =
'missing_file'`, which a scan sets when a file merely vanishes.

**Override**:
A user edit to a track's title, artist, album, track number, disc number or
year, stored in `track_overrides` and written through to the `tracks` columns.
The scanner re-applies overrides after reading file tags; the audio file is
never written. Reset to file tags deletes the override rows.
_Avoid_: Tag edit (the file's tags are untouched).

**Stale timings**:
A track whose lyrics were edited while an `.lrc` exists: `lyrics_state.state =
'manual' AND synced = 0` and the `.lrc` file is on disk. The synced route then
serves plain text with `lrc_stale: true`, and the listener keeps the old
timings or removes them.

**Handoff**:
The switch from the playing `<audio>` element to the preloaded standby one,
used for gapless playback and crossfade. It preloads only when the next track
is deterministic: not under shuffle and not under repeat-one.

**Launch chain**:
The sequence `run_chain` in `muse/api.py` runs at startup and again after every
watcher event, folder change or `POST /api/scan`: scan, lyrics sync, art
backfill, and (launch only, when the last retrain is over a day old) a taste
retrain. One `asyncio.Lock` serializes it because the shared connection cannot
hold two transactions.

**Leg**:
One term in the recommender's `rank()` fusion sum — a similarity score
between a query track and a candidate track, computed from one signal source
(CLAP audio, Spotify-style features, lyrics, emotion, taste, MERT content) and
weighted before summing. The two audio-content legs — CLAP `audio_vec` and
MERT `content_vec` — share one weight in the sum rather than holding one
each (ADR 0004).
_Avoid_: Signal, feature, factor (too general — "leg" is specific to a term
inside the fusion formula).

**Embedding batch**:
The resumable, per-track job (`jobs.kind = 'embed'`) that computes and stores
a track's `audio_vec` (CLAP), `feature_vec` (min-max normalized Spotify
features), and `lyrics_vec` (MiniLM) into the `embeddings` table.
`embeddings.audio_vec IS NULL` is the work queue, so a killed run resumes
rather than restarting; a `lyrics_hash` mismatch on an already-embedded track
re-embeds just that track's lyrics leg. Distinct from the retrain job, which
is whole-library and schedule-triggered rather than per-track and
source-triggered.
_Avoid_: Retrain job (see below — that term is reserved for the taste
embedding's nightly, whole-library recompute).

**Encoder protocol**:
The `AudioEncoder`/`LyricsEncoder` seam (`muse/embed.py`) that lets the embed
batch, and everything that tests it, run against a deterministic stub
(hash of input → fixed-dimension, L2-normalized vector) instead of the real
CLAP/MiniLM models. The same seam covers `AudioVAEncoder`/`LyricsVAEncoder`
(real: `MerAudioVAEncoder`/`TextVAEncoder`; stub: hash of input → an
unnormalized 2D valence-arousal point, since VA is a bounded plane, not a
direction). `muse/embed.py` is the only module that imports torch, and only
inside the real encoders' `__init__` — never at module import time — so the
default test run and the app itself never pay for it unless
`config.embed_batch` is on.
_Avoid_: Model wrapper (too generic — the point of the name is the seam,
not that it wraps a model).

**Emotion vec**:
A 2D valence-arousal point per track (`embeddings.emotion_vec`), fused from
audio-VA (`embeddings.audio_va`, a pretrained music-emotion-recognition
model run on the audio file — always available, computed alongside
`audio_vec` in the same embed-batch pass) and lyrics-VA
(`embeddings.lyrics_va`, a text-based emotion model run on the cleaned
lyrics — null for instrumentals), falling back to audio-VA alone when
lyrics are absent. Compared by `reco.emotion_sim`, not `reco.cosine_sim` —
see Valence-arousal / VA below for why.
_Avoid_: Mood vector, sentiment (sentiment implies text-only; this is fused
audio+text).

**Content vec**:
A 768-dimension per-track vector (`content_embeddings.content_vec`) from
pretrained MERT-v1-95M, computed by `universal_reco` — a separate project
with its own virtualenv that `muse` never imports, invoked as a subprocess
batch (`python -m muse.content_embed_batch`). Stored **raw**, not
L2-normalized like `audio_vec` and `lyrics_vec`: MERT's output is
anisotropic, so `reco.content_sim` subtracts the corpus mean
(`content_stats.mean_vec`) before comparing and rescales through two
measured percentile anchors. Shares one audio-content weight budget with
CLAP's `audio_vec` rather than getting its own (ADR 0004).
_Avoid_: Audio vec (that name is taken, by the CLAP leg — these are two
different embeddings of the same signal), MERT vec (name the role, not the
checkpoint, so replacing the model does not rename the concept).

**Valence-arousal / VA**:
The two-axis emotion representation this project uses: valence (positive↔
negative) and arousal (calm↔energetic). Two points close together in this
plane represent similar moods, compared by Euclidean distance — not by angle
from the origin, which is why `emotion_sim` differs from the recommender's
other, cosine-based legs.
_Avoid_: Valence alone (Spotify's `feature_vec.valence` is one axis only and
is a different, pre-existing signal — see the PRD).

**Feedback score**:
`muse.feedback.feedback_score(conn, track_rowid, *, now)` — a pure,
time-decayed sum over one track's `plays` and `ratings` history (skip/
full_listen/replay weights, `exp(-0.1 * days_since)` decay, thumbs_down
folded in at its own weight), clamped to [-3, 3] and normalized to [-1, 1].
Originally specified as a `rank()` leg; ADR 0003 repurposes it as the taste
embedding's sole training input instead, so it no longer appears in the
fusion sum on its own.
_Avoid_: Rating (ratings is one input among several, not the whole thing).

**Taste embedding**:
A per-track vector learned by implicit-ALS matrix factorization over the
owner's own listening history (`feedback_score`, aggregated library-wide).
Captures co-occurrence patterns — tracks the owner tends to play together or
in sequence — that no per-track content signal can. Replaces `feedback_score`
as a `rank()` leg once enough play history exists; null before that point.
_Avoid_: Collaborative filtering (there is only one user here — this is
matrix factorization over one person's own behavior, not a cross-user
signal), user embedding (it's per-track, not per-user).

**Retrain job**:
The nightly batch job that recomputes the taste embedding for the whole
library at once from current listening history, replacing the previous
snapshot wholesale. Distinct from the resumable, per-track, on-ingest
embedding batch the PRD already defines for CLAP/lyrics/feature vectors.
_Avoid_: Embedding batch (that term is reserved for the per-track, source-
triggered job; the retrain job is whole-library and schedule-triggered).

**Audio term**:
The single fused audio-content score, `a_clap · clap_sim + a_content ·
content_sim`, worth one weight (`w_audio`) no matter how many audio legs
contributed. Not two legs with two weights — ADR 0004 binds this. When the
content leg is unavailable its share reverts to CLAP rather than
renormalizing outward, so the term stays worth exactly `w_audio` on the
tracks where CLAP is the only audio evidence there is.

**Calibration**:
Each leg's raw similarity mapped through that leg's own empirical CDF over
random track pairs, so a typical pair scores 0.5 on every leg before
fusion (ADR 0008). What makes a null leg dropping out of the renormalized
average neutral rather than a bias. Distinct from the content leg's
`sim_lo`/`sim_hi` rescale, which happens first and still does.
_Avoid_: Normalization (ambiguous with renormalizing the weights).

**Offline harness**:
`muse/reco_eval.py`: replays consecutive plays as (seed, next) transitions
and reports where the recommender ranks the real next track. The thing O8
says must exist before any scoring change ships.

**Coverage gate**:
The library-wide switch that decides whether the content leg may score at
all: `content_stats.track_count / playable ≥ CONTENT_MIN_COVERAGE`. Reads the
statistics row rather than the number of stored vectors, because vectors
commit per track while the anchors are rewritten only at the end of a batch.
Library-wide rather than per-track because content absence is ordered by
`track_rowid`, so a partial batch would otherwise become a systematic boost
for early imports. The caller turns it into `rank()`'s `content_stats`
argument — `None` means gated.

**Offer count**:
How many times the recommender has *returned* a track as a candidate. Not a
play. Lives in `track_offers` because `plays` holds physical fact (ADR 0001)
and a suggestion is not one. Freshness decays to zero after three offers, so
a track you keep declining stops being pushed. Derived and disposable:
clearing the table costs a few sessions of ranking quality and nothing else.

**Relaxation ladder**:
The fixed order in which `/api/recommend` radio gives up constraints when
candidates run dry — freshness, then artist spacing from 3 to 1, then the
exclusion set. The arc engine has its own (`muse/arc.py` `LADDER`, or
`PLAIN_LADDER` for an untagged seed): filler, then freshness, then artist
spacing. Darkness and theme are cost inside outer bounds, not rungs (ADR 0010);
the thumbs-down, version dedupe, glorifying and heavy-content rules are never
relaxed. Never
fails to return a track; each rung taken is named in `reasons["relaxed"]` so
the degradation is visible. An explicit thumbs-down is never relaxed: the
ladder loosens constraints that encode library size, and a rejection is not
one of those.

**Radio session**:
A stretch of listening in which the client keeps its own queue topped up by
asking `GET /api/recommend/` (or, not yet wired into the UI, `GET /api/arc/`
with `mode=radio`) for one more track. Both are stateless: there is no
server-side session object and no server-side queue — "the session" is
`player_state.radio_seed` (the track it started from; NULL means radio is
off) plus the client's rolling exclusion set. The seed is stored rather than
a bare on/off flag because it is the one thing a listener asks about a
session an hour in, and it sits in `player_state` beside `shuffle` and
`repeat_mode` so radio survives a restart like the other two modes do.
_Avoid_: Radio mode, radio queue (the queue is *the* queue — radio appends to
it and owns nothing separate).

**Query point**:
The track a radio append asks from — the `{track_rowid}` path parameter of
`GET /api/recommend/`. It is the **tail of the queue**, not what is currently
playing, so each pick is derived from the pick before it and an hour of
listening migrates away from the seed instead of orbiting it (story 36). The
rolling exclusion set (`exclude=`) is what stops it doubling back; `recent=`
is a separate, shorter list, most recent first, and exists only for artist
spacing to measure against.
_Avoid_: Seed (that is the *start* of the session and does not move).

**Mood target**:
A normalized `[valence, arousal]` pair, each in `[-1, 1]`, sent to
`GET /api/recommend/` as `mood=valence,arousal`. The emotion leg measures
distance to it instead of to the query point's own emotion vec. It is mapped
library-relative: 0 is the per-axis median of the library's emotion vecs and
+1 / -1 are the 95th / 5th percentile (`recommend.mood_target`). Omitting it
leaves scores unchanged.
_Avoid_: Mood (the stored emotion vec and the mood bands are different things).

**Muse pick**:
A queue entry the recommender added, as opposed to one the listener chose. The
ids are carried in `player_state.radio_added` (`radio_added` on the wire). A
re-rank toward a new mood target replaces only the Muse picks.
_Avoid_: Radio track.

**Playlist**:
A fixed, ordered list of tracks the listener saved, held in `playlists` and
`playlist_tracks` (migration 19). It is not a query: it does not adapt to the
library and survives re-embedding and retrains unchanged.
_Avoid_: Mix, station (those regenerate; a playlist does not).

**Reasons payload**:
The per-candidate `reasons` object every recommendation carries: each
surviving leg's raw similarity, its weight after renormalization and their
product; which legs were null and why; the audio split actually used; the
freshness contribution and offer count; and any relaxation ladder rung taken.
It is what makes a bad recommendation diagnosable — it names the weight that
caused the pick instead of inviting a blind nudge (story 37) — and the client
renders it through copy maps rather than showing the raw keys.

**Discovery**:
The MusicBrainz-backed browse path (`muse/discover.py`) — artist,
release groups, tracklist — and the only one. It hands `POST /api/jobs` one of
two kinds to queue: `"mb"` for a single track (facts already on the request) or
`"mb_release"` for a whole record (resolved server-side), both of which fan out
to ordinary `download` job rows. Distinct from library search, which reads what
is already downloaded. It is the one MusicBrainz path that runs inline with a
request rather than as a background job, because it is a person typing and
waiting.
_Avoid_: Catalog search (the fork has no catalog mirror to search).

**Owned-track identity**:
Whether the library already holds a recording: its MusicBrainz recording id,
its ISRC, or casefolded artist + title (`muse/titles.py:same_title`) within
3 s of duration. One rule, shared by the tracklist's in-library badge and
`find_done` (both through `find_held`).

**Archetype**:
_Retired by ADR 0009 (2026-10-17); kept for history._
A named radio profile authored by answering a fixed question ("what do you
play when you are sad / in the car / on a plane"). `archetype_id` is a stable
slug, never reused or renumbered, so the question set can gain new questions
without invalidating anything that points at the old ones. Each archetype
pairs a seed set with a drift (`hold` or `lead`) and, for `lead`, a
`target_id` naming the archetype its query point travels toward. Stored in
`archetypes` (migration `_m014_archetypes`; the module that read it is gone)
(ADR 0005).
_Avoid_: Playlist (an archetype generates query points at request time; it
isn't a stored, ordered list of tracks — the seed set is the closer analogue,
and even that isn't one).

**Seed set**:
_Retired by ADR 0009 (2026-10-17); kept for history._
The tracks a listener names as their answer to an archetype's question
(`archetype_seeds`, `source = 'user'`), plus any they accepted from a
suggestion (`source = 'suggested'`). A song may sit in more than one
archetype's seed set. The removed recall script held back `user`-sourced
seeds only when measuring recall — grading against `suggested` seeds would
have the model grade its own homework.
_Avoid_: Training set (seeds aren't fit to anything; they're averaged into a
centroid — see archetype centroid).

**Drift**:
_Retired by ADR 0009 (2026-10-17); kept for history._
An archetype's `hold` or `lead` mode — one mechanism, parameterised, not two
code paths (ADR 0005), covering both the situational lists that should stay
close to their seeds and the emotional lists that should travel toward a
better mood. `hold` fixes the ramp step at zero with a tight cone
(`CONE_HOLD`); `lead` takes a non-zero step toward the archetype's `target_id`
with a loose cone (`CONE_LEAD`). Skips still steer the query point inside a
`hold` session — the cone bounds where it can go, it doesn't freeze it there.
_Avoid_: Mode (already means something else here — `player_state`'s
`shuffle`/`repeat_mode` — and this isn't that).

**Ramp (`t`)**:
_Retired by ADR 0009 (2026-10-17); kept for history._
The scalar in `[0, 1]` that blends an archetype's starting centroid and its
target centroid — `0` is the start, `1` is the target. Derived fresh on every
`GET /api/radio/block` call from the session's `plays` rows since the
recipe's `since` timestamp (ADR 0005, D9), never accumulated on the client: a
full listen advances it, an early skip retreats it, a late skip or an
unclassifiable span (`classify_play`'s `unknown`) leaves it unchanged. A pure
function of `(archetype, session_start, plays)`, computed in the retired `drift` module.
_Avoid_: Progress (this isn't monotonic session progress — a skip moves it
backward).

**Cone**:
_Retired by ADR 0009 (2026-10-17); kept for history._
The clamp that keeps a drifting query point from wandering off-archetype: the
query point is kept within a minimum similarity (`CONE_HOLD`/`CONE_LEAD`) of
the **current point on the path** — not the starting centroid — checked per
leg. Cosine legs are compared by cosine similarity; `emotion_vec` is compared
by Euclidean distance on the bounded VA plane (ADR 0003), mapped onto the
same [0, 1] similarity scale the cosine legs use, so one threshold
(`CONE_HOLD` or `CONE_LEAD`) serves every leg. Computed in the retired `drift` module
alongside the blend and the nudge it clamps.
_Avoid_: Radius (on the cosine legs the bound is an angle, not a distance).

**Archetype centroid**:
_Retired by ADR 0009 (2026-10-17); kept for history._
The mean of an archetype's seed-set vectors, one per leg the seeds actually
carry — audio, content, feature, lyrics, emotion, taste — rather than a query
point built from the content leg alone, so a query point built from it still
has every leg `rank()` expects live (ADR 0005, D5).
A leg no seed carries (an all-instrumental archetype has no lyrics centroid)
stays absent on the query point, the same null-leg contract `feature_vec` and
`taste_sim` already have. Computed on demand from `load_vectors()`'s output,
fresh on every call — no stored table, no migration, no in-process cache
either (D10, amended): a cache keyed on the archetype row can't see
`taste_embeddings` being rewritten nightly, in a separate process, into an
unaligned basis.
_Avoid_: Query point (the centroid is one fixed input to the blend; the query
point is what `blend()` and `nudge()` produce from it, and it moves session to
session while the centroid doesn't).

**Curator**:
_Retired by ADR 0009 (2026-10-17); kept for history._
The stage after retrieval, the retired `curator` module, that turns `recommend.radio_block()`'s
fifty-candidate ranked pool into a twelve-track ordered block by asking a
System One decision model one `score` question per remaining candidate, one
round per chosen track — never a single batched pass, which cannot see the
prefix already chosen (ADR 0006, D2). Can only reorder and drop from what
retrieval already returned, so a curation win is a win over ordering, never
over recall.
_Avoid_: Re-ranker (the earlier draft's name — dropped because it implies
reordering a fixed list in one pass, not the sequential, prefix-aware loop
this actually is).

**System One backend**:
A model that answers typed `questions` against a `state` with probabilities
and generates no text — `choice`, `score`, or `noul` — reached over
`POST /v1/systemone`, one wire protocol both vendors speak. Jev is hosted
(`https://api.typesafe.ai`, a paid model id required); Laya is local
(`laya-serve`, no key, no model). `muse/systemone.py` is the one HTTP client
for both; the A/B between them is that one client against two base URLs,
never two clients that could drift apart.
_Avoid_: LLM (neither model generates text; System One primitives return
typed answers with probabilities, not prose).

**Archetype profile**:
_Retired by ADR 0009 (2026-10-17); kept for history._
One stored number per track per archetype (`archetype_profiles`) — `reco.rank()`
with the archetype centroid as the query and the song as the only candidate
— read by the curator's state builder rather than recomputed per block
(ADR 0006, D6). Null, not a number, when the archetype is below
`recommend.LEAD_TARGET_SEED_FLOOR` (3) usable seeds, via the same
`archetypes.usable_seed_count()` the drift route's own floor check uses.
_Avoid_: Archetype centroid (that is the vector the profile score is computed
against, not the score itself — see Archetype centroid above).

**Taste stamp**:
_Retired by ADR 0009 (2026-10-17); kept for history._
`profiles.taste_stamp()`'s `MAX(trained_at)` across `taste_embeddings`, or 0
when the table is empty. The generation marker an `archetype_profiles` row
is compared against: a mismatch means the nightly retrain has rewritten
every `taste_embeddings` row into a fresh, unaligned ALS basis since the row
was computed, which stales every profile column at once rather than one
(ADR 0006, D6's invalidation rule, alongside `seed_hash` for a changed seed
set).
_Avoid_: Version (there is no counter to bump and nothing to remember — the
stamp is read fresh from the table it names, the same way `computed_at`
timestamps already work elsewhere in this codebase).

**Lyric theme**:
_Retired by ADR 0009 (2026-10-17); kept for history._
One locally generated sentence (`lyric_themes.theme`) saying what a song is
about, written by a quantized instruct model's own batch job so nothing
leaves the machine (D10) — the curator's `score` questions have no room for
full lyrics, and a hosted model to summarize them would widen the boundary
the retired curator spec's S2 set. Invalidated the same way the
lyrics leg is: a stored `lyrics_hash` that no longer matches
`embeddings.lyrics_hash` means a repaired lyric, and the theme goes back on
the work queue.
_Avoid_: Summary (a theme is one sentence for a curator prompt, not a
general-purpose lyric summary).

**Tag set**:
One song's lyric tags at one taxonomy version: a `lyric_tag_sets` row plus
its raw probabilities in `lyric_tags`, read as
`{segment: {group: {label: prob}}}`. A set tagged from prefetched lyrics
before the song's audio landed has no `track_rowid` yet and waits, keyed by
`recording_mbid`, until `muse.lyric_tag_batch` links it. A `no_lines` set is
an answer (nothing left to tag after cleaning), not a missing one.
_Avoid_: Labels, annotations; and "lyric theme", which is the older
one-sentence summary.

**Segment**:
A part of a song asked separately when tagging: `hook` (the repeated lines,
each kept once), `start`/`middle`/`end` (thirds of the sung span by
timestamp), or a single `body` when there are too few lines to split.
`song` holds the questions asked once on the whole lyric (setting, mode, the
severity flags).
_Avoid_: Section, verse (a segment is cut by repetition and time, not by the
song's own verse/chorus labels).

**Taxonomy version**:
`muse.lyric_tags.TAXONOMY_VERSION`, stored on every tag set. Raising it puts
every set from an older version back in the batch's work queue, so a
re-worded taxonomy re-tags the library with no migration.

**Outlook**:
The per-segment tag group that places a part of a song between darkness and
light: despair → struggling → turning → overcome → untroubled, plus
`harm_as_fun` off that ladder. "Darkness" means real pain, so heartbreak and
grief count; a crush or gentle wistfulness does not.
_Avoid_: Mood, valence (the emotion leg's VA plane is a different thing).

**Mood darkness**:
How hopeless a song feels, from 0 to 1: the outlook ladder's expected
position, averaged over segments (`muse.lyric_tags.scores`). It is computed
at read time, never stored. It is one of three negative scores that are kept
apart on purpose. A single combined score called flag-free heartbreak light
and a glorifying drug-party song light.

**Heavy content**:
The worst of six severity flags (drugs/alcohol, self-harm/suicide, violence,
abuse, sexual, death), each graded absent / mention / theme / graphic and
read as a 0–1 expectation. Only literal depictions count; a metaphor is at
most a mention.

**Glorifying**:
The mean probability of outlook `harm_as_fun`: a song that makes something
harmful look fun or cool. Kept separate from mood darkness, because such
songs are usually not sad.

**Arc**:
The darkness curve a session follows: hold at the seed's starting darkness
for the first `HOLD_FRAC` of the span, then turn and step down (at most
`STEP_CAP` per song) to `END_TARGET`. A radio uses `RADIO_HORIZON` songs as
its span and holds the last value after it. Each pick's darkness must sit
within a window of the current target. Pure maths in `muse/arc.py`, applied
by `muse/arc_engine.py` (ADR 0009).
_Avoid_: Drift (the retired archetype mechanism moved a query point; the arc
moves a darkness target and never touches the query).

**Curve** / **target**:
The curve is the list of per-position darkness targets (`arc.curve()`); the
target is its value at one position (`arc.target()`), held at the last value
past the end.

**Anchor**:
The subject vector every candidate's subject similarity is measured against
(`theme_sim`, floored at `THEME_FLOOR`), not against the previous pick, so a
session cannot wander off-topic one small step at a time. It blends from the
start subject to the goal subject with the curve's progress; with start equal
to goal it is the seed's subject vector, fixed for the whole session.

**Request spec**:
The one shape the arc planner reads (`muse/request_spec.py`, `VERSION` 1): a
kind, a start and a goal, a shape (`steady`, `gradual`, `late`), a sound
(`calm`, `mid`, `energetic`, `any`) and limits (`anything`, `no_explicit`,
`clean`). `POST /api/arc` validates it and echoes it back; a radio sends it
again with each call.

**Start and goal**:
The two ends of a request spec, each a subject (tag weights, or none) and a
darkness (or none). The curve runs from the start darkness to the goal
darkness and the anchor from the start subject to the goal subject. A seed
session has start equal to goal in subject.

**Cost**:
What a pick costs the planner (`arc.pick_cost`; lower is better): `ALPHA` times
the squared distance from the darkness target, `BETA` times theme
dissimilarity to the anchor, `GAMMA` times lost continuity with the previous
track, `DELTA` times the gap to the asked-for sound band. A filler takes
`FILLER_COST` in place of the first two terms (ADR 0010).

**Beam**:
The planner's search (`arc.beam_search`, run by `arc_engine.plan`): it keeps
`BEAM_WIDTH` partial paths per step; width 1 is greedy. Its paths are grouped
into lanes by fillers placed, and the beam fills round-robin across lanes,
cheapest first, so equal-cost permutations of interchangeable picks do not
crowd out filler-spaced paths. A pick that took a relaxed rung adds
`RELAX_COST` per rung name, so a path that needed the ladder loses to an equal
one that did not.

**Horizon**:
How many picks a plan covers. A playlist plans its `n`; a radio plans
`max(2n, 8)` from `pos`, returns the first `n`, and keeps nothing, so the next
call replans from the real `recent`. Distinct from `RADIO_HORIZON`, the span
over which a radio's darkness curve lifts.

**Subject vector**:
A song's lyric-tag vector without the outlook, emotion, stance and severity
groups (`arc.subject_vector()`): what it is about, not how dark it is. With
the mood groups in, the theme filter would hold darkness steady and fight
the lift.

**Filler**:
A pick with no tag set, so no darkness to place on the curve. At most one in
`FILLER_EVERY` (4) picks may be filler (`arc.filler_ok()`), and it is
reported as `filler` in the pick's reasons.

**Version key**:
What makes two tracks the same song for session dedupe: the same
`title_key` (title with live/remaster/version/edit/acoustic/demo/mono/feat
markers stripped) with a shared artist, or the same lyric `text_hash`
whoever sings it (`arc.same_version()`).
