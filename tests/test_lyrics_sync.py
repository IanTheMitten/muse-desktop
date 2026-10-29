"""`muse/lyrics_lrclib.py`, `muse/library/lyrics_state.py`, and
`jobs.run_lyrics_sync_backfill` — the LRCLIB sync batch (PRD "Synced
lyrics", O9/O10).

No network anywhere here: every test that reaches `sync_batch` or
`run_lyrics_sync_backfill` injects a fake `lrclib_search`, matching the
factory-injection pattern `tests/test_jobs_embed.py` already uses for the
embed batch's encoders. `muse/lyrics_sync.py` is the only caller of the real
`search_lrclib`, and nothing here imports it.
"""

import asyncio

from muse.jobs import JobBus, run_lyrics_sync_backfill
from muse.library import jobs as job_rows
from muse.library import lyrics_state
from muse.lrc import strip_timestamps
from muse.lyrics_lrclib import (
    LYRICS_SYNC,
    SyncOutcome,
    decide_outcome,
    pick_best,
    sync_batch,
)
from tests.conftest import insert_track

SYNCED_LRC = (
    "[00:12.00] walking down this empty road again tonight\n"
    "[00:16.50] thinking of the words you never said to me\n"
    "[00:20.00] and the light is fading slow behind the hills\n"
    "[00:24.00] i keep on waiting for a sign that never comes"
)
SYNCED_LRC_DERIVED = (
    "walking down this empty road again tonight\n"
    "thinking of the words you never said to me\n"
    "and the light is fading slow behind the hills\n"
    "i keep on waiting for a sign that never comes"
)
TOO_SHORT_LRC = "[00:01.00] hi"
PLAIN_TEXT = (
    "walking down this empty road again tonight thinking of the words "
    "you never said to me and the light is fading slow over the hills"
)


def _candidate(**overrides):
    base = {
        "id": 1,
        "trackName": "Some Song",
        "artistName": "Some Artist",
        "albumName": "Some Album",
        "duration": 200,
        "instrumental": False,
        "plainLyrics": None,
        "syncedLyrics": None,
    }
    base.update(overrides)
    return base


def _track(**overrides):
    base = {
        "track_rowid": 1,
        "artist_name": "Some Artist",
        "album_name": "Some Album",
        "track_name": "Some Song",
        "duration_ms": 200_000,
        "is_instrumental": 0,
        "file_path": None,
    }
    base.update(overrides)
    return base


# --- pick_best --------------------------------------------------------


def test_pick_best_prefers_synced_over_plain_at_equal_distance():
    plain = _candidate(duration=198, syncedLyrics=None, plainLyrics="text")
    synced = _candidate(duration=202, syncedLyrics="[00:01.00] a", plainLyrics=None)
    assert pick_best([plain, synced], 200_000) == synced


def test_pick_best_prefers_closer_duration_when_both_synced():
    far = _candidate(duration=195, syncedLyrics="[00:01.00] a")
    near = _candidate(duration=199, syncedLyrics="[00:01.00] a")
    assert pick_best([far, near], 200_000) == near


def test_pick_best_rejects_everything_outside_tolerance():
    far = _candidate(duration=100, syncedLyrics="[00:01.00] a")
    assert pick_best([far], 200_000) is None


def test_pick_best_accepts_exactly_five_seconds_off():
    edge = _candidate(duration=195, syncedLyrics="[00:01.00] a")
    assert pick_best([edge], 200_000) == edge


def test_pick_best_returns_none_on_no_candidates():
    assert pick_best([], 200_000) is None
    assert pick_best([_candidate()], None) is None


# --- strip_timestamps (muse.lrc) -----------------------------------------


def test_strip_lrc_timestamps_derives_plain_text():
    assert strip_timestamps(SYNCED_LRC) == SYNCED_LRC_DERIVED


def test_strip_lrc_timestamps_drops_metadata_lines():
    lrc = "[ar:Some Artist]\n[ti:Some Song]\n[00:12.00] a lyric line here"
    assert strip_timestamps(lrc) == "a lyric line here"


# --- decide_outcome: the full state machine -----------------------------


def test_decide_synced_and_valid_writes_both_files():
    track = _track()
    candidate = _candidate(syncedLyrics=SYNCED_LRC)
    outcome = decide_outcome(track, [candidate])
    assert outcome.state == "synced"
    assert outcome.synced is True
    assert outcome.lyrics_text == SYNCED_LRC_DERIVED
    assert outcome.lrc_text is not None
    assert SYNCED_LRC in outcome.lrc_text


def test_decide_synced_but_invalid_derived_text_keeps_lrc_only():
    track = _track()
    candidate = _candidate(syncedLyrics=TOO_SHORT_LRC)
    outcome = decide_outcome(track, [candidate])
    assert outcome.state == "synced"
    assert outcome.synced is True
    assert outcome.lrc_text is not None
    # Deliberately not written -- validation failing means "found but not
    # better than silence", and a synced-but-invalid text must never
    # silently overwrite whatever `.lyrics.txt` currently holds.
    assert outcome.lyrics_text is None


def test_decide_plain_and_valid_overwrites_lyrics_txt():
    track = _track()
    candidate = _candidate(plainLyrics=PLAIN_TEXT)
    outcome = decide_outcome(track, [candidate])
    assert outcome.state == "plain"
    assert outcome.synced is False
    assert outcome.lyrics_text == PLAIN_TEXT
    assert outcome.lrc_text is None


def test_decide_plain_but_invalid_touches_nothing():
    track = _track()
    candidate = _candidate(plainLyrics="hi")
    outcome = decide_outcome(track, [candidate])
    assert outcome.state == "plain"
    assert outcome.synced is False
    assert outcome.lyrics_text is None
    assert outcome.lrc_text is None


def test_decide_not_found_when_nothing_matches():
    track = _track(is_instrumental=0)
    outcome = decide_outcome(track, [])
    assert outcome == SyncOutcome(state="not_found", synced=False)


def test_decide_declared_instrumental_via_lrclib_flag():
    track = _track()
    candidate = _candidate(instrumental=True, syncedLyrics=None, plainLyrics=None)
    outcome = decide_outcome(track, [candidate])
    assert outcome == SyncOutcome(state="declared_instrumental", synced=False)


def test_decide_declared_instrumental_via_filename_fallback():
    """No LRCLIB candidate at all, but the filename heuristic
    (`tracks.is_instrumental`) already flagged this track — O9's fallback."""
    track = _track(is_instrumental=1)
    outcome = decide_outcome(track, [])
    assert outcome == SyncOutcome(state="declared_instrumental", synced=False)


def test_decide_lrclib_instrumental_flag_wins_even_if_filename_says_otherwise():
    """O9: LRCLIB's explicit flag is strictly better than the heuristic and
    takes priority whenever it has an opinion at all."""
    track = _track(is_instrumental=0)
    candidate = _candidate(instrumental=True)
    outcome = decide_outcome(track, [candidate])
    assert outcome.state == "declared_instrumental"


# --- the manual guard -----------------------------------------------------


def test_pending_excludes_a_manual_track(conn):
    insert_track(conn, 1, status="done")
    insert_track(conn, 2, status="done")
    lyrics_state.upsert(conn, 1, state="manual", synced=0, checked_at=1)

    queue = lyrics_state.pending(conn)
    assert {row["track_rowid"] for row in queue} == {2}


def test_sync_batch_never_calls_search_for_a_manual_track(conn, monkeypatch, tmp_path):
    """Defense in depth on top of `pending()`'s own exclusion: even if the
    work queue were somehow made to include a manual row, `sync_batch`'s own
    guard must stop it before a network call is ever attempted."""
    insert_track(conn, 1, status="done")
    insert_track(conn, 2, status="done")
    lyrics_state.upsert(conn, 1, state="manual", synced=0, checked_at=1)

    manual_row = {
        "track_rowid": 1,
        "artist_name": "Some Artist",
        "album_name": "Some Album",
        "track_name": "Some Song",
        "duration_ms": 200_000,
        "is_instrumental": 0,
        "file_path": None,
    }
    real_pending = lyrics_state.pending

    def rigged_pending(c):
        # Force the manual track back into the queue, simulating a bug in
        # `pending()`'s own filter -- the loop's own guard must still catch
        # it.
        return [manual_row, *real_pending(c)]

    monkeypatch.setattr(
        "muse.lyrics_lrclib.library_lyrics_state.pending", rigged_pending
    )

    calls = []

    def fake_search(*, track_name, artist_name, album_name):
        calls.append((track_name, artist_name, album_name))
        return []

    sync_batch(conn, fake_search, lyrics_root=tmp_path / "lyrics", sleep=lambda _s: None)

    # Only track 2 was ever a legitimate candidate for a query; track 1's
    # manual state means it must be skipped before `fake_search` is invoked
    # at all, which this asserts by checking the call count matches exactly
    # the non-manual track count.
    assert len(calls) == 1

    manual_state = conn.execute(
        "SELECT state FROM lyrics_state WHERE track_rowid = 1"
    ).fetchone()
    assert manual_state["state"] == "manual"


# --- sync_batch: writes, tallies, rate limiting ----------------------------


def test_sync_batch_writes_files_and_records_state(conn, tmp_path):
    audio_path = tmp_path / "song.m4a"
    audio_path.write_bytes(b"")
    insert_track(conn, 1, status="done", duration_ms=200_000)
    conn.execute(
        "UPDATE tracks SET file_path = ? WHERE track_rowid = 1", (str(audio_path),)
    )

    def fake_search(*, track_name, artist_name, album_name):
        return [_candidate(duration=200, syncedLyrics=SYNCED_LRC)]

    sleeps = []
    result = sync_batch(
        conn,
        fake_search,
        lyrics_root=tmp_path / "lyrics",
        rate_limit_seconds=0.1,
        sleep=sleeps.append,
    )

    assert result.considered == 1
    assert result.synced == 1
    assert sleeps == [0.1]

    lrc_path = tmp_path / "lyrics" / "1.lrc"
    lyrics_path = tmp_path / "lyrics" / "1.txt"
    assert lrc_path.exists()
    assert lyrics_path.read_text(encoding="utf-8") == SYNCED_LRC_DERIVED

    row = conn.execute(
        "SELECT state, synced FROM lyrics_state WHERE track_rowid = 1"
    ).fetchone()
    assert row["state"] == "synced"
    assert row["synced"] == 1


def test_sync_batch_backfills_tracks_lyrics_path_when_absent(conn, tmp_path):
    """`insert_track` leaves `lyrics_path` NULL by default -- exactly the
    ~19-of-723 population the PRD's own measurement names. Without pointing
    `tracks.lyrics_path` at the file this batch just wrote, `read_lyrics`
    and the embed batch's lyrics-changed pass have no way to find it, so the
    fetch would be invisible everywhere downstream of this call."""
    audio_path = tmp_path / "song.m4a"
    audio_path.write_bytes(b"")
    insert_track(conn, 1, status="done", duration_ms=200_000)
    conn.execute(
        "UPDATE tracks SET file_path = ? WHERE track_rowid = 1", (str(audio_path),)
    )
    assert conn.execute(
        "SELECT lyrics_path FROM tracks WHERE track_rowid = 1"
    ).fetchone()["lyrics_path"] is None

    def fake_search(*, track_name, artist_name, album_name):
        return [_candidate(duration=200, plainLyrics=PLAIN_TEXT)]

    sync_batch(conn, fake_search, lyrics_root=tmp_path / "lyrics", sleep=lambda _s: None)

    row = conn.execute(
        "SELECT lyrics_path FROM tracks WHERE track_rowid = 1"
    ).fetchone()
    assert row["lyrics_path"] == str(tmp_path / "lyrics" / "1.txt")


def test_sync_batch_never_overwrites_an_existing_lyrics_path(conn, tmp_path):
    audio_path = tmp_path / "song.m4a"
    audio_path.write_bytes(b"")
    insert_track(conn, 1, status="done", duration_ms=200_000)
    conn.execute(
        "UPDATE tracks SET file_path = ?, lyrics_path = 'manifest/path.lyrics.txt'"
        " WHERE track_rowid = 1",
        (str(audio_path),),
    )

    def fake_search(*, track_name, artist_name, album_name):
        return [_candidate(duration=200, plainLyrics=PLAIN_TEXT)]

    sync_batch(conn, fake_search, lyrics_root=tmp_path / "lyrics", sleep=lambda _s: None)

    row = conn.execute(
        "SELECT lyrics_path FROM tracks WHERE track_rowid = 1"
    ).fetchone()
    assert row["lyrics_path"] == "manifest/path.lyrics.txt"


def test_sync_batch_isolates_a_decide_failure_to_one_track(conn, tmp_path):
    """A malformed LRCLIB response (a `duration` that is not a number) must
    be counted and skipped, not raised out of the batch -- the same failure
    isolation `run_embed_batch` and `art.backfill` already give their own
    per-item work. Both tracks trigger it here; what matters is that the
    second track is still visited (`considered` and `failed` both land on
    2) rather than the run stopping after the first raise."""
    insert_track(conn, 1, status="done")
    insert_track(conn, 2, status="done")

    def malformed_search(*, track_name, artist_name, album_name):
        return [{"duration": "not-a-number", "instrumental": False,
                  "plainLyrics": None, "syncedLyrics": None}]

    result = sync_batch(conn, malformed_search, lyrics_root=tmp_path / "lyrics", sleep=lambda _s: None)

    assert result.considered == 2
    assert result.failed == 2


def test_sync_batch_counts_a_search_failure_without_stopping(conn, tmp_path):
    insert_track(conn, 1, status="done")
    insert_track(conn, 2, status="done")

    def flaky_search(*, track_name, artist_name, album_name):
        raise ConnectionError("lrclib is down")

    result = sync_batch(conn, flaky_search, lyrics_root=tmp_path / "lyrics", sleep=lambda _s: None)
    assert result.considered == 2
    assert result.failed == 2


# --- library.lyrics_state.upsert -----------------------------------------


def test_upsert_round_trips(conn):
    insert_track(conn, 1, status="done")
    lyrics_state.upsert(conn, 1, state="plain", synced=False, checked_at=123)
    row = conn.execute(
        "SELECT * FROM lyrics_state WHERE track_rowid = 1"
    ).fetchone()
    assert row["state"] == "plain"
    assert row["synced"] == 0
    assert row["lrc_offset_ms"] == 0
    assert row["checked_at"] == 123


def test_upsert_preserves_offset_when_omitted_on_rerun(conn):
    insert_track(conn, 1, status="done")
    lyrics_state.upsert(
        conn, 1, state="synced", synced=True, checked_at=100, lrc_offset_ms=250
    )
    # A later batch re-run (e.g. after a manual lyrics edit clears `synced`)
    # omits lrc_offset_ms entirely -- it must not reset a listener's nudge.
    lyrics_state.upsert(conn, 1, state="synced", synced=False, checked_at=200)

    row = conn.execute(
        "SELECT lrc_offset_ms, checked_at FROM lyrics_state WHERE track_rowid = 1"
    ).fetchone()
    assert row["lrc_offset_ms"] == 250
    assert row["checked_at"] == 200


# --- run_lyrics_sync_backfill --------------------------------------------


def test_run_lyrics_sync_backfill_creates_a_done_job(conn, tmp_path):
    insert_track(conn, 1, status="done", duration_ms=200_000)
    # the default relative MUSIC/ path would write into the real library from the repo root
    conn.execute(
        "UPDATE tracks SET file_path = ? WHERE track_rowid = 1",
        (str(tmp_path / "song.m4a"),),
    )

    def fake_search(*, track_name, artist_name, album_name):
        return [_candidate(duration=200, plainLyrics=PLAIN_TEXT)]

    bus = JobBus()
    job = asyncio.run(
        run_lyrics_sync_backfill(
            conn,
            bus,
            lyrics_root=tmp_path / "lyrics",
            lrclib_search=fake_search,
            rate_limit_seconds=0,
        )
    )

    assert job is not None
    assert job["state"] == "done"
    assert job["kind"] == LYRICS_SYNC

    stored = job_rows.get(conn, job["id"])
    assert stored["kind"] == LYRICS_SYNC
    assert stored["state"] == "done"

    row = conn.execute(
        "SELECT state FROM lyrics_state WHERE track_rowid = 1"
    ).fetchone()
    assert row["state"] == "plain"


def test_run_lyrics_sync_backfill_is_a_noop_when_already_running(conn, tmp_path):
    insert_track(conn, 1, status="done")
    job_rows.create(conn, LYRICS_SYNC)  # left "queued" by a prior run

    def fake_search(*, track_name, artist_name, album_name):
        raise AssertionError("must not be called while a batch is running")

    bus = JobBus()
    result = asyncio.run(
        run_lyrics_sync_backfill(
            conn, bus, lyrics_root=tmp_path / "lyrics", lrclib_search=fake_search
        )
    )

    assert result is None
