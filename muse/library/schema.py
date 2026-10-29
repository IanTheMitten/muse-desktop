"""The migration ladder for library.sqlite3.

A plain ordered list of functions plus a `schema_version` log, applied at
startup. No migration framework: one database, one machine, one writer.

The ladder exists from day one because after slice 1 this database holds play
history, and "delete and re-import" stops being an available answer.

Rules for anyone adding to it:
  - Append. Never edit a migration that has shipped.
  - Never add an interpretive column to `plays` (see ADR 0001).
  - Do not create tables for a future slice. `prompts` belongs to the slice
    that justifies it; `embeddings` is slice 2's own (ADR 0003 / ticket 01)
    and `lyrics_state` is slice 4's own (migration 12).
"""

import sqlite3
import time
from collections.abc import Callable

SCHEMA_SQL = """
CREATE TABLE tracks (
    track_rowid   INTEGER PRIMARY KEY,      -- catalog tracks.rowid, or local id >= 1e9
    track_id      TEXT UNIQUE,              -- spotify id; NULL for post-freeze tracks
    origin        TEXT NOT NULL,            -- 'catalog' | 'local'
    metadata_tier TEXT NOT NULL,            -- 'catalog' | 'musicbrainz' | 'ytdlp'
    artist_rowid  INTEGER,
    album_rowid   INTEGER,
    artist_name   TEXT NOT NULL,
    artist_names  TEXT NOT NULL,            -- json array
    album_name    TEXT NOT NULL,
    track_name    TEXT NOT NULL,
    track_no      INTEGER,
    disc_no       INTEGER,
    duration_ms   INTEGER,
    isrc          TEXT,
    file_path     TEXT,
    lyrics_path   TEXT,
    art_path      TEXT,
    gain_db       REAL,                     -- NULL until slice 2; NULL means unity gain
    is_instrumental INTEGER NOT NULL DEFAULT 0,
    source_site_id  TEXT,
    status        TEXT NOT NULL,            -- 'done' | 'error' | 'missing_file'
    error         TEXT,
    added_at      INTEGER NOT NULL
);

CREATE INDEX tracks_artist  ON tracks(artist_name);
CREATE INDEX tracks_album   ON tracks(album_rowid);
CREATE INDEX tracks_status  ON tracks(status);

CREATE TABLE plays (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    track_rowid   INTEGER NOT NULL REFERENCES tracks(track_rowid),
    started_at    INTEGER NOT NULL,   -- unix ms
    ended_at      INTEGER,            -- NULL while playing; set on end or by the sweeper
    reached_ms    INTEGER NOT NULL,   -- furthest playback position reached
    duration_ms   INTEGER NOT NULL,   -- track length at play time
    ended_reason  TEXT,               -- 'track_end'|'user_next'|'user_prev'|'new_selection'
                                      -- |'pause_timeout'|'lost'
    seek_count    INTEGER NOT NULL DEFAULT 0,
    source        TEXT,               -- 'library'|'queue'|'radio'|'recommend'
    heartbeat_at  INTEGER NOT NULL    -- last heartbeat; the sweeper reads this
);

CREATE INDEX plays_track   ON plays(track_rowid);
CREATE INDEX plays_started ON plays(started_at);
CREATE INDEX plays_open    ON plays(ended_at) WHERE ended_at IS NULL;

-- Explicit signals only. Everything else is derived from `plays`.
CREATE TABLE ratings (
    track_rowid   INTEGER PRIMARY KEY REFERENCES tracks(track_rowid),
    rating        INTEGER NOT NULL,   -- -1 thumbs down, +1 thumbs up
    created_at    INTEGER NOT NULL
);

-- Single-row table. The queue is server-side so it follows you between devices.
CREATE TABLE player_state (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    queue         TEXT NOT NULL,      -- json array of track_rowid, in order
    queue_index   INTEGER NOT NULL,
    position_ms   INTEGER NOT NULL,
    shuffle       INTEGER NOT NULL DEFAULT 0,
    repeat_mode   TEXT NOT NULL DEFAULT 'off',  -- 'off'|'one'|'all'
    radio_seed    INTEGER,            -- unused in slice 1; slice 2 fills it
    updated_at    INTEGER NOT NULL,
    updated_by    TEXT                -- device label, for debugging only
);

CREATE TABLE jobs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT NOT NULL,   -- slice 1: 'art_backfill'
    payload       TEXT NOT NULL,   -- json
    state         TEXT NOT NULL,   -- 'queued' | 'running' | 'done' | 'error' | 'cancelled'
    progress      REAL NOT NULL DEFAULT 0,
    message       TEXT,
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL
);

-- Search is over this library only -- 711 rows -- and never over the catalog.
-- Trigram tokenization buys substring and typo tolerance without a second
-- dependency. The `norm_*` columns hold normalize_title() output: index entries,
-- never display values.
CREATE VIRTUAL TABLE tracks_fts USING fts5(
    track_name, artist_name, album_name,
    norm_track_name, norm_artist_name, norm_album_name,
    tokenize = 'trigram'
);
"""


def _statements(script: str) -> list[str]:
    """Split a SQL script into statements.

    Used instead of `executescript`, which commits any pending transaction
    before it runs and would therefore break the atomicity the ladder depends
    on. `sqlite3.complete_statement` does the splitting, so quoting and
    semicolons inside literals are handled by SQLite's own parser rather than
    by a regex.
    """
    statements, buffer = [], ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statements.append(buffer.strip())
            buffer = ""
    if buffer.strip():
        statements.append(buffer.strip())
    return statements


ALBUM_ART_SQL = """
-- What the art backfill has already decided about an album.
--
-- `tracks.art_path` answers "what do I show for this track"; this table answers
-- "have I already asked". They are different questions, and only this one can
-- record a *negative* — an album Cover Art Archive genuinely has no cover for.
-- Without it, the 87-album backfill would re-ask about every failure on every
-- restart forever, which is exactly the "fetched once, ever" property the slice
-- asks for.
CREATE TABLE album_art (
    album_rowid INTEGER PRIMARY KEY,
    art_path    TEXT,             -- NULL means "asked, and there is none"
    source      TEXT,             -- 'coverartarchive' | NULL
    mbid        TEXT,             -- the release the art came from
    checked_at  INTEGER NOT NULL
);
"""


TRACK_FEATURES_SQL = """
-- What the Spotify audio-features mirror says about a track we own.
--
-- A copy, not a join. The mirror is a 29 GB read-only file and the dashboard
-- reads these numbers on every load; materialising 723 rows here means the
-- request path opens one database instead of two, and the mirror is asked
-- about a track exactly once, ever.
--
-- A row with NULL measurements means "asked, and the mirror has nothing" —
-- the same distinction `album_art` draws between an album with no cover and an
-- album nobody has looked for yet. Without it the 18 tracks the mirror does not
-- carry would be re-asked at every startup forever.
CREATE TABLE track_features (
    track_rowid      INTEGER PRIMARY KEY REFERENCES tracks(track_rowid),
    valence          REAL,   -- 0..1, musical positiveness
    energy           REAL,   -- 0..1
    danceability     REAL,   -- 0..1
    acousticness     REAL,   -- 0..1
    instrumentalness REAL,   -- 0..1
    tempo            REAL,   -- bpm
    loudness         REAL,   -- dBFS
    mode             INTEGER,-- 1 major, 0 minor
    checked_at       INTEGER NOT NULL
);

-- The dashboard's mood bands are ranges over these two, and nothing else.
CREATE INDEX track_features_mood ON track_features(valence, energy);
"""


RECOMMENDER_FEATURE_COLUMNS_SQL = """
-- The rest of what the mirror measured. m003 only materialised valence and
-- energy for the dashboard's mood bands; the embed batch's 13-dim feature_vec
-- (ADR 0003 / ticket 01) needs the other four. `null_response = 1` on the
-- mirror already comes through as every column NULL, so no separate flag is
-- needed here -- a NULL `speechiness` means the same "asked, and there is
-- nothing" that a NULL `valence` already means.
ALTER TABLE track_features ADD COLUMN speechiness REAL;
ALTER TABLE track_features ADD COLUMN liveness REAL;
ALTER TABLE track_features ADD COLUMN key INTEGER;
ALTER TABLE track_features ADD COLUMN time_signature INTEGER;
"""


EMBEDDINGS_SQL = """
-- One content vector per source, per track. `embeddings.audio_vec IS NULL` is
-- the embed batch's resumable work queue: a track with no row here, or one
-- with an unset audio_vec, has not been embedded yet, and the batch restarts
-- into that set rather than from the top every time it is killed.
--
-- A row can carry a NULL feature_vec or NULL lyrics_vec and still count as
-- "embedded" -- a null_response feature row and an instrumental with no
-- lyrics file are real answers, not missing work. Only audio_vec gates the
-- queue, because CLAP reads the audio file directly and every playable track
-- has one.
CREATE TABLE embeddings (
    track_rowid   INTEGER PRIMARY KEY REFERENCES tracks(track_rowid),
    audio_vec     BLOB,            -- float32, 512d CLAP, L2-normalized
    feature_vec   BLOB,            -- float32, 13d min-max normalized
    lyrics_vec    BLOB,            -- float32, 384d MiniLM, L2-normalized
    audio_model   TEXT,
    lyrics_model  TEXT,
    lyrics_hash   TEXT,            -- sha256 of the text embedded; re-embed on change
    indexed_at    INTEGER NOT NULL
);
"""


FEATURE_NORM_BOUNDS_SQL = """
-- The min/max feature_vec was normalized against, frozen the first time the
-- embed batch has anything measured to normalize against and never rescaled
-- after. Without this, one new track with an extreme loudness would silently
-- redefine the scale for every vector already stored, and a freshly computed
-- vector would stop being comparable to an old one -- the same problem
-- `album_art` and `track_features` solve by caching a decision instead of
-- re-deriving it every time.
CREATE TABLE feature_norm_bounds (
    column     TEXT PRIMARY KEY,
    min_value  REAL NOT NULL,
    max_value  REAL NOT NULL
);
"""


EMOTION_COLUMNS_SQL = """
-- audio-VA and lyrics-VA feed the fused `emotion_vec` (ADR 0003 / ticket 02):
-- a 2D valence-arousal point, giving mood matching a real arousal axis that
-- feature_vec's single valence scalar never had. audio_va is set whenever
-- audio_vec is (the MER model reads the same audio file CLAP does); lyrics_va
-- is NULL for an instrumental, and emotion_vec then falls back to audio_va
-- alone rather than going NULL too.
ALTER TABLE embeddings ADD COLUMN audio_va BLOB;
ALTER TABLE embeddings ADD COLUMN lyrics_va BLOB;
ALTER TABLE embeddings ADD COLUMN emotion_vec BLOB;
ALTER TABLE embeddings ADD COLUMN audio_va_model TEXT;
ALTER TABLE embeddings ADD COLUMN lyrics_va_model TEXT;
"""


TASTE_EMBEDDINGS_SQL = """
-- The nightly ALS retrain's whole-library snapshot (ADR 0003 / ticket 04):
-- one row per playable track, overwritten wholesale by
-- `library.taste.replace_all` every retrain, never a per-row upsert from
-- anywhere else. `taste_vec` is NULL for a track below
-- `muse.taste.MIN_PLAY_EVENTS` -- a real answer, not missing work, the same
-- contract `embeddings.feature_vec` already has for a post-freeze track.
CREATE TABLE taste_embeddings (
    track_rowid   INTEGER PRIMARY KEY REFERENCES tracks(track_rowid),
    taste_vec     BLOB,           -- float32, TASTE_DIM-d implicit-ALS factor
    model         TEXT,           -- 'implicit-als' | 'stub-als'
    trained_at    INTEGER NOT NULL
);
"""


CONTENT_EMBED_SQL = """
-- The MERT content leg (spec 2026-08-24-content-embed-leg-design). Its own
-- table rather than a column on `embeddings` for two structural reasons:
-- `embeddings.upsert` is documented as writing every field explicitly on the
-- premise that one process recomputes every leg at once, which a vector
-- produced by a separate program in a separate virtualenv cannot satisfy;
-- and `embeddings.pending` would otherwise be two work queues with two
-- owners reading one row. A track with no row here, or a row whose
-- `content_vec` is NULL, is unembedded -- that disjunction is this leg's
-- whole work queue. The batch only ever writes a non-NULL vector and skips
-- a failed track entirely rather than writing a row for it, so the NULL
-- case exists to make the predicate total, not to record a state this code
-- produces -- unlike `taste_embeddings.taste_vec` (below the play-event
-- threshold) or `embeddings.feature_vec` (a post-freeze track), a NULL here
-- names no steady state.
CREATE TABLE content_embeddings (
    track_rowid   INTEGER PRIMARY KEY REFERENCES tracks(track_rowid),
    content_vec   BLOB,           -- float32, CONTENT_DIM-d raw MERT vector
    model         TEXT,           -- 'm-a-p/MERT-v1-95M' | 'stub-content'
    embedded_at   INTEGER NOT NULL
);

-- Corpus statistics for the leg's normalization, one row, id = 1. Raw MERT
-- vectors are anisotropic -- they share a dominant mean component, which
-- compresses every cosine into a narrow band -- so `reco.content_sim`
-- centers by `mean_vec` before comparing and rescales the result through
-- the `sim_lo`/`sim_hi` percentile anchors. Rewritten by every batch run;
-- `track_count` and `computed_at` are what make a stale mean visible.
CREATE TABLE content_stats (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    mean_vec      BLOB NOT NULL,
    sim_lo        REAL NOT NULL,
    sim_hi        REAL NOT NULL,
    track_count   INTEGER NOT NULL,
    computed_at   INTEGER NOT NULL
);
"""


# An *offer* is the recommender returning a track as a candidate. O2's
# freshness boost decays after a track has been offered three times and
# declined, which needs a count of offers -- and nothing in the system had
# one. This is deliberately not a column on `tracks` and deliberately not a
# row in `plays`: `plays` holds physical fact (ADR 0001) and "we suggested
# this" is not one, while `tracks` holds what the library is rather than what
# the recommender has been doing lately.
#
# It is a derived convenience count. Clearing the whole table costs a few
# sessions of ranking quality and loses nothing that cannot be rebuilt by
# using the app, which is why it carries no history and no backup guarantee
# -- `tests/test_offers.py` asserts that resetting it leaves the module
# working rather than leaving the claim in a comment.
OFFER_COUNTS_SQL = """
CREATE TABLE track_offers (
    track_rowid   INTEGER PRIMARY KEY REFERENCES tracks(track_rowid),
    offer_count   INTEGER NOT NULL DEFAULT 0,
    last_offered  INTEGER NOT NULL
);
"""


# State about the `.lyrics.txt` / `.lrc` pair on disk -- never the text or the
# timings themselves. Those stay files (story 58): portable to any other
# player, and the one thing that survives this table being dropped and
# rebuilt. `state` is the slice-4 worklist status: 'synced' (LRCLIB gave
# timings), 'plain' (text only, no sync found), 'declared_instrumental' (O9 --
# LRCLIB's flag or the filename heuristic; leaves the worklist for good),
# 'not_found' (no LRCLIB match at all), or 'manual' (story 54 -- a human
# edited this track's lyrics in the browser). No row at all means "never
# checked", which is what makes absence the batch's work queue, the same
# shape as `embeddings.pending`.
#
# `manual` is the one state this table exists to protect: every future
# automated pass -- a re-run of the sync batch, a provider re-fetch -- must
# skip a track in that state rather than overwrite a correction someone made
# by hand. Nothing sets it yet (the editor is a later ticket), but the column
# is load-bearing from the first batch run, not added when the editor lands,
# because a batch that ships before the guard it needs would have nothing
# stopping it from clobbering the first manual edit made after.
#
# `synced` is O10's display precedence, not a duplicate of `state = 'synced'`:
# an edit to a synced track's text clears it (the `.lrc` timings no longer
# necessarily line up with the edited lines) while `state` can still say
# `manual`. `lrc_offset_ms` is the per-track nudge (stories 68, 69) -- applied
# at render time only, never written back into the `.lrc` file, so resetting
# it is free and the file on disk never drifts from what LRCLIB returned.
LYRICS_STATE_SQL = """
CREATE TABLE lyrics_state (
    track_rowid    INTEGER PRIMARY KEY REFERENCES tracks(track_rowid),
    state          TEXT NOT NULL,
    synced         INTEGER NOT NULL DEFAULT 0,
    lrc_offset_ms  INTEGER NOT NULL DEFAULT 0,
    checked_at     INTEGER NOT NULL
);
"""


TRACK_MBIDS_SQL = """
ALTER TABLE tracks ADD COLUMN recording_mbid TEXT;
ALTER TABLE tracks ADD COLUMN release_mbid TEXT;
CREATE INDEX tracks_recording_mbid ON tracks(recording_mbid)
    WHERE recording_mbid IS NOT NULL;
CREATE INDEX tracks_release_mbid ON tracks(release_mbid)
    WHERE release_mbid IS NOT NULL;
"""


ARCHETYPES_SQL = """
CREATE TABLE archetypes (
  archetype_id  TEXT PRIMARY KEY,
  prompt        TEXT    NOT NULL,
  drift         TEXT    NOT NULL CHECK (drift IN ('hold', 'lead')),
  target_id     TEXT    REFERENCES archetypes(archetype_id),
  position      INTEGER NOT NULL,
  hidden        INTEGER NOT NULL DEFAULT 0,
  created_at    INTEGER NOT NULL,
  updated_at    INTEGER NOT NULL
);

CREATE TABLE archetype_seeds (
  archetype_id  TEXT    NOT NULL REFERENCES archetypes(archetype_id),
  track_rowid   INTEGER NOT NULL,
  source        TEXT    NOT NULL CHECK (source IN ('user', 'suggested')),
  added_at      INTEGER NOT NULL,
  PRIMARY KEY (archetype_id, track_rowid)
);
"""


CURATOR_SQL = """
-- The curator's three tables land together because they arrive together:
-- an archetype's per-track fit score, the lyric-theme tag that feeds it, and
-- the log of what the drift curator actually chose.
CREATE TABLE archetype_profiles (
  track_rowid   INTEGER NOT NULL REFERENCES tracks(track_rowid),
  archetype_id  TEXT    NOT NULL REFERENCES archetypes(archetype_id),
  -- NULL means the archetype was below recommend.LEAD_TARGET_SEED_FLOOR when
  -- this row was computed -- a fact about the archetype, not a missing value
  -- to be filled in later (D8).
  score         REAL,
  -- `seed_hash` and `taste_stamp` are two independent staleness signals: a
  -- seed edit stales one archetype's column, a nightly retrain stales every
  -- column, and the two stamps are what tell those apart without anything
  -- having to be remembered (D7).
  seed_hash     TEXT    NOT NULL,
  taste_stamp   INTEGER NOT NULL,
  computed_at   INTEGER NOT NULL,
  PRIMARY KEY (track_rowid, archetype_id)
);

CREATE TABLE lyric_themes (
  track_rowid   INTEGER PRIMARY KEY REFERENCES tracks(track_rowid),
  theme         TEXT,
  lyrics_hash   TEXT,
  model         TEXT,
  written_at    INTEGER NOT NULL
);

-- `archetype_id` deliberately carries no foreign key: this is a log of what
-- happened, and a deleted archetype must not erase or block the record of
-- blocks it produced. The four TEXT list columns hold JSON arrays.
CREATE TABLE curator_blocks (
  id            INTEGER PRIMARY KEY,
  backend       TEXT    NOT NULL,
  archetype_id  TEXT    NOT NULL,
  drift_t       REAL    NOT NULL,
  target_id     TEXT,
  candidates    TEXT    NOT NULL,
  chosen        TEXT    NOT NULL,
  round_ms      TEXT    NOT NULL,
  confidences   TEXT    NOT NULL,
  fallbacks     INTEGER NOT NULL,
  failed        INTEGER NOT NULL DEFAULT 0,
  created_at    INTEGER NOT NULL
);
"""

# Taxonomy-tag probabilities per song (docs/specs/2026-10-12-lyric-tag-store.md).
LYRIC_TAGS_SQL = """
CREATE TABLE lyric_tag_sets (
  id                INTEGER PRIMARY KEY,
  -- NULL until the song's audio lands: a set tagged from prefetched lyrics
  -- waits here keyed by recording_mbid, then is linked by the batch (D1, D4).
  track_rowid       INTEGER UNIQUE REFERENCES tracks(track_rowid),
  recording_mbid    TEXT,
  state             TEXT    NOT NULL,   -- 'tagged' | 'no_lines'
  -- Two hashes, two jobs (D2): `text_hash` is the segmenter's input lines
  -- ("were these the lyrics that were tagged?"); `lyrics_hash` is
  -- embeddings.lyrics_hash when written ("were they repaired since?").
  text_hash         TEXT    NOT NULL,
  lyrics_hash       TEXT,
  taxonomy_version  INTEGER NOT NULL,
  model             TEXT    NOT NULL,
  written_at        INTEGER NOT NULL
);

CREATE INDEX lyric_tag_sets_unlinked
  ON lyric_tag_sets(recording_mbid) WHERE track_rowid IS NULL;

-- Raw probabilities only; every derived score is computed at read time
-- (muse/lyric_tags.py), so a formula change never needs a re-ask.
CREATE TABLE lyric_tags (
  set_id   INTEGER NOT NULL REFERENCES lyric_tag_sets(id) ON DELETE CASCADE,
  segment  TEXT    NOT NULL,   -- 'song' | 'hook' | 'start' | 'middle' | 'end' | 'body'
  grp      TEXT    NOT NULL,
  label    TEXT    NOT NULL,
  prob     REAL    NOT NULL,
  PRIMARY KEY (set_id, segment, grp, label)
);
"""


def _m001_initial(conn: sqlite3.Connection) -> None:
    for statement in _statements(SCHEMA_SQL):
        conn.execute(statement)


def _m002_album_art(conn: sqlite3.Connection) -> None:
    for statement in _statements(ALBUM_ART_SQL):
        conn.execute(statement)


def _m003_track_features(conn: sqlite3.Connection) -> None:
    for statement in _statements(TRACK_FEATURES_SQL):
        conn.execute(statement)


def _m004_embeddings(conn: sqlite3.Connection) -> None:
    for statement in _statements(EMBEDDINGS_SQL):
        conn.execute(statement)


def _m005_recommender_feature_columns(conn: sqlite3.Connection) -> None:
    for statement in _statements(RECOMMENDER_FEATURE_COLUMNS_SQL):
        conn.execute(statement)


def _m006_feature_norm_bounds(conn: sqlite3.Connection) -> None:
    for statement in _statements(FEATURE_NORM_BOUNDS_SQL):
        conn.execute(statement)


def _m007_emotion_columns(conn: sqlite3.Connection) -> None:
    for statement in _statements(EMOTION_COLUMNS_SQL):
        conn.execute(statement)


def _m008_taste_embeddings(conn: sqlite3.Connection) -> None:
    for statement in _statements(TASTE_EMBEDDINGS_SQL):
        conn.execute(statement)


def _m009_content_embed(conn: sqlite3.Connection) -> None:
    for statement in _statements(CONTENT_EMBED_SQL):
        conn.execute(statement)


def _m010_offer_counts(conn: sqlite3.Connection) -> None:
    for statement in _statements(OFFER_COUNTS_SQL):
        conn.execute(statement)


def _m011_retry_audio_va(conn: sqlite3.Connection) -> None:
    """Let the audio half of the emotion leg be asked for again.

    `AUDIO_VA_MODEL` named a model id that does not exist on the Hub, so
    every real embed run fell through to `NullAudioVAEncoder` and stamped
    `audio_va_model = 'unavailable'`. That stamp is what stops
    `embeddings.pending` re-queueing the whole library on every restart, and
    it did its job -- but it also means correcting the id changes nothing:
    all 723 rows are permanently marked as asked-and-answered for a question
    that was never actually put to a model.

    Clearing the stamp puts them back in the work queue exactly once. Only
    rows carrying that sentinel are touched, so a row embedded by a real
    model -- or by a stub, in a test -- keeps the name of whatever answered
    it. `audio_va` itself is already NULL on every one of these rows; there
    is no measurement here to lose.
    """
    conn.execute(
        "UPDATE embeddings SET audio_va_model = NULL"
        " WHERE audio_va_model = 'unavailable'"
    )


def _m012_lyrics_state(conn: sqlite3.Connection) -> None:
    for statement in _statements(LYRICS_STATE_SQL):
        conn.execute(statement)


def _m013_track_mbids(conn: sqlite3.Connection) -> None:
    """MusicBrainz identity for tracks the catalog mirror never described.

    `recording_mbid` is the dedup key `find_done` needs: a post-freeze track
    has no `track_rowid` until the download allocates one, no
    `source_site_id` until yt-dlp has run, and frequently no ISRC at all --
    MusicBrainz often carries none for weeks after a release appears. Without
    a fourth key, queueing the same track twice downloads it twice.

    `release_mbid` is what a local `album_rowid` is allocated against, so
    every track of one release shares one album, and it is the Cover Art
    Archive's own key -- which matters because the existing art path walks
    ISRC to recording to release, and these are exactly the albums with no
    ISRC to start that walk from.

    Both indexes are partial: only a few hundred rows will ever be non-null
    against a library that is otherwise entirely catalog-sourced.

    Neither is UNIQUE. A failed mb download leaves a `status='error'` row
    still carrying its recording_mbid, and retrying it allocates a fresh
    local rowid rather than reusing that row -- a UNIQUE index would turn
    every retry of a failed post-freeze download into an IntegrityError.
    Uniqueness belongs at the query layer instead: `find_done` matches
    `status='done'` only, precisely so a failed attempt is never mistaken
    for a reason to skip a re-download.
    """
    for statement in _statements(TRACK_MBIDS_SQL):
        conn.execute(statement)


def _m014_archetypes(conn: sqlite3.Connection) -> None:
    """The radio drift policy's archetype question set (S3).

    `archetype_id` is a stable slug and is never reused or renumbered, which
    is what makes the question set upgradeable. `position` orders the
    questions on screen and carries no other meaning. `hidden` retires a
    question without deleting the answers. `source` separates picks the
    listener made from suggestions they accepted, because the recall script
    of D8 must measure against the former only -- otherwise the model grades
    its own homework.
    """
    for statement in _statements(ARCHETYPES_SQL):
        conn.execute(statement)


def _m015_curator(conn: sqlite3.Connection) -> None:
    for statement in _statements(CURATOR_SQL):
        conn.execute(statement)


def _m016_player_radio_added(conn: sqlite3.Connection) -> None:
    """Which queued tracks radio put there, as a JSON array of rowids.

    The client tags a play `source='radio'` from this set, and the set used
    to live only in the tab's memory: a reload, or picking the queue up on
    the other device, restored the queue but forgot it, so radio picks were
    recorded as `queue`. The offline harness (ADR 0008) reads `source` to
    keep the recommender's own picks from flattering it.
    """
    conn.execute(
        "ALTER TABLE player_state"
        " ADD COLUMN radio_added TEXT NOT NULL DEFAULT '[]'"
    )


def _m017_lyric_tags(conn: sqlite3.Connection) -> None:
    """Lyric tags, taxonomy v3 (docs/specs/2026-10-12-lyric-tag-store.md).

    Two tables, not one: a song tagged before its audio landed has a set but
    no track yet, and a `no_lines` song is an answer with zero tag rows.
    """
    for statement in _statements(LYRIC_TAGS_SQL):
        conn.execute(statement)


def _m018_retire_archetypes(conn: sqlite3.Connection) -> None:
    """ADR 0009: the arc engine replaces archetype radio, its drift, the
    curator, archetype profiles and one-sentence lyric themes. Children
    before parents, because foreign keys are on and immediate:
    `archetype_profiles` and `archetype_seeds` reference `archetypes`."""
    for table in ("archetype_profiles", "archetype_seeds", "archetypes",
                  "lyric_themes", "curator_blocks"):
        conn.execute(f"DROP TABLE IF EXISTS {table}")


PLAYLISTS_SQL = """
CREATE TABLE playlists (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE TABLE playlist_tracks (
    playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    track_rowid INTEGER NOT NULL REFERENCES tracks(track_rowid) ON DELETE CASCADE,
    PRIMARY KEY (playlist_id, position)
);
CREATE INDEX playlist_tracks_track ON playlist_tracks(track_rowid);
"""


def _m019_playlists(conn: sqlite3.Connection) -> None:
    """Playlists are fixed ordered track lists, not queries: they survive
    retrains unchanged. Duplicates are allowed, hence position as the key."""
    for statement in _statements(PLAYLISTS_SQL):
        conn.execute(statement)


def _m020_reask_missing_art(conn: sqlite3.Connection) -> None:
    """Decision 5 C: the new chain gets one more ask at the 66 albums.

    Forget the "no cover" answers; found covers are untouched.
    """
    conn.execute("DELETE FROM album_art WHERE art_path IS NULL")


def _m021_local_files(conn: sqlite3.Connection) -> None:
    """The folder scanner's identity columns (desktop Decision 9) and the tombstone M2's delete sets (Decision 10)."""
    conn.execute("ALTER TABLE tracks ADD COLUMN file_size INTEGER")
    conn.execute("ALTER TABLE tracks ADD COLUMN file_mtime INTEGER")
    conn.execute("ALTER TABLE tracks ADD COLUMN format TEXT")
    conn.execute("ALTER TABLE tracks ADD COLUMN album_key TEXT")
    conn.execute("ALTER TABLE tracks ADD COLUMN removed_at INTEGER")
    conn.execute("CREATE INDEX tracks_file_path ON tracks(file_path)")
    conn.execute("CREATE INDEX tracks_album_key ON tracks(album_key)")


# Append only. Index in this list + 1 is the version a migration produces.
MIGRATIONS: list[Callable[[sqlite3.Connection], None]] = [
    _m001_initial,
    _m002_album_art,
    _m003_track_features,
    _m004_embeddings,
    _m005_recommender_feature_columns,
    _m006_feature_norm_bounds,
    _m007_emotion_columns,
    _m008_taste_embeddings,
    _m009_content_embed,
    _m010_offer_counts,
    _m011_retry_audio_va,
    _m012_lyrics_state,
    _m013_track_mbids,
    _m014_archetypes,
    _m015_curator,
    _m016_player_radio_added,
    _m017_lyric_tags,
    _m018_retire_archetypes,
    _m019_playlists,
    _m020_reask_missing_art,
    _m021_local_files,
]

LATEST_VERSION = len(MIGRATIONS)


def current_version(conn: sqlite3.Connection) -> int:
    """0 on a database that has never been migrated."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ).fetchone()
    if row is None:
        return 0
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return row[0] or 0


def migrate(conn: sqlite3.Connection) -> int:
    """Apply every pending migration in order. Returns the resulting version.

    A no-op on an up-to-date database. Each migration and the version row it
    produces commit together, so a ladder that fails halfway leaves the database
    at the last version that fully applied rather than in a state no code
    expects.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "  version INTEGER NOT NULL,"
        "  applied_at INTEGER NOT NULL)"
    )

    version = current_version(conn)
    for index in range(version, len(MIGRATIONS)):
        target = index + 1
        conn.execute("BEGIN")
        try:
            MIGRATIONS[index](conn)
            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (target, int(time.time())),
            )
        except Exception:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    return current_version(conn)
