"""The batch driver and the corpus-statistics recompute.

Everything here runs against `StubContentEmbedder` or a purpose-built fake:
no subprocess, no torch, no `universal_reco`. That is the same seam
`StubAudioEncoder` and `StubTasteTrainer` provide for the other legs.
"""

import pytest

from muse import content
from muse.library import content as library_content
from tests.conftest import insert_track


class FailingEmbedder:
    """Succeeds for every track except the ones named, which come back as
    error results — the shape `content_embed.batch` emits for a corrupt
    file."""

    model_name = "fake"

    def __init__(self, failing: set[int]):
        self.failing = failing

    def embed_many(self, items):
        for track_rowid, _path in items:
            if track_rowid in self.failing:
                yield track_rowid, None, "No audio samples decoded"
            else:
                yield track_rowid, [float(track_rowid), 1.0, 0.0], None


class InterleaveCheckingEmbedder:
    """Before yielding item *i*, records whether item *i - 1* is already
    visible through `library_content.vectors` on the same connection --
    this observes the "committed before the next result is requested"
    interleaving directly, rather than inferring it from final counts,
    which a batch that drained the generator into a list first would also
    satisfy."""

    model_name = "interleave-check"

    def __init__(self, conn):
        self.conn = conn
        self.previous_was_committed: list[bool] = []

    def embed_many(self, items):
        for i, (track_rowid, _path) in enumerate(items):
            if i > 0:
                previous_rowid = items[i - 1][0]
                committed = library_content.vectors(self.conn)
                self.previous_was_committed.append(previous_rowid in committed)
            yield track_rowid, [float(track_rowid), 0.0, 0.0], None


def test_batch_embeds_every_pending_track(conn):
    insert_track(conn, 1)
    insert_track(conn, 2)
    result = content.run_content_batch(conn, content.StubContentEmbedder())
    assert result.embedded == 2
    assert result.failed == 0
    assert set(library_content.vectors(conn)) == {1, 2}


def test_a_second_run_has_nothing_left_to_do(conn):
    """`content_vec IS NULL` is the whole queue, so a completed track never
    comes back — this is what makes the batch resumable rather than
    restartable."""
    insert_track(conn, 1)
    content.run_content_batch(conn, content.StubContentEmbedder())
    result = content.run_content_batch(conn, content.StubContentEmbedder())
    assert result.embedded == 0


def test_a_failed_track_stays_on_the_queue(conn):
    insert_track(conn, 1)
    insert_track(conn, 2)
    result = content.run_content_batch(conn, FailingEmbedder({2}))
    assert result.embedded == 1
    assert result.failed == 1
    assert [row["track_rowid"] for row in library_content.pending(conn)] == [2]


def test_each_result_is_committed_before_the_next_is_requested(conn):
    """The docstring's "a kill mid-batch costs one track" claim rests on
    `run_content_batch` consuming `embed_many` one result at a time and
    committing each before asking the generator for the next -- an
    implementation that drained the generator into a list first would pass
    every other test in this file while breaking exactly this property."""
    insert_track(conn, 1)
    insert_track(conn, 2)
    insert_track(conn, 3)
    embedder = InterleaveCheckingEmbedder(conn)
    content.run_content_batch(conn, embedder)
    assert embedder.previous_was_committed == [True, True]


def test_progress_is_reported_per_track(conn):
    insert_track(conn, 1)
    insert_track(conn, 2)
    seen = []
    content.run_content_batch(
        conn,
        content.StubContentEmbedder(),
        on_progress=lambda done, total, message: seen.append((done, total)),
    )
    assert seen == [(1, 2), (2, 2)]


def test_the_batch_records_which_model_produced_each_vector(conn):
    insert_track(conn, 1)
    content.run_content_batch(conn, content.StubContentEmbedder())
    row = conn.execute(
        "SELECT model FROM content_embeddings WHERE track_rowid = 1"
    ).fetchone()
    assert row["model"] == content.STUB_CONTENT_MODEL


def test_the_batch_recomputes_the_corpus_statistics(conn):
    for rowid in range(1, 6):
        insert_track(conn, rowid)
    content.run_content_batch(conn, content.StubContentEmbedder())
    stats = library_content.stats(conn)
    assert stats is not None
    assert stats["track_count"] == 5
    assert len(stats["mean_vec"]) == content.CONTENT_DIM
    assert stats["sim_lo"] < stats["sim_hi"]


def test_the_mean_is_the_componentwise_average_of_every_vector(conn):
    insert_track(conn, 1)
    insert_track(conn, 2)
    insert_track(conn, 3)
    library_content.upsert(
        conn, 1, content_vec=content.pack_vector([0.0, 4.0]), model="m", now=1
    )
    library_content.upsert(
        conn, 2, content_vec=content.pack_vector([2.0, 0.0]), model="m", now=1
    )
    library_content.upsert(
        conn, 3, content_vec=content.pack_vector([4.0, 2.0]), model="m", now=1
    )
    content.recompute_stats(conn)
    assert library_content.stats(conn)["mean_vec"] == [2.0, 2.0]


def test_stats_are_left_alone_when_there_is_nothing_stored(conn):
    """One track cannot produce a pair, so there is no distribution to take
    percentiles of. Writing anchors from no data would be worse than having
    none."""
    content.recompute_stats(conn)
    assert library_content.stats(conn) is None


def test_two_tracks_cannot_calibrate_the_leg(conn):
    """The corpus mean of two vectors sits exactly between them, so the
    centered pair are exact negatives and the only similarity in the
    corpus is -1.0. That is a point, not a distribution -- the leg stays
    unavailable rather than storing anchors that would make every
    `content_sim` call raise."""
    insert_track(conn, 1)
    insert_track(conn, 2)
    content.run_content_batch(conn, content.StubContentEmbedder())
    assert library_content.stats(conn) is None


def test_a_corpus_with_no_spread_leaves_the_leg_unavailable(conn):
    """Enough vectors, but identical ones -- every pair scores the same, so
    the two anchors coincide. `reco.content_sim` raises on a degenerate
    range, so this is the guard that keeps such a row from ever existing."""
    for rowid in (1, 2, 3, 4):
        insert_track(conn, rowid)
        library_content.upsert(
            conn, rowid, content_vec=content.pack_vector([1.0, 1.0]),
            model="m", now=1,
        )
    content.recompute_stats(conn)
    assert library_content.stats(conn) is None


def test_the_anchors_bracket_the_centered_cosine_distribution(conn):
    """`sim_lo`/`sim_hi` are percentiles of the centered pairwise cosines,
    so nearly every pair's score should land inside them."""
    for rowid in range(1, 21):
        insert_track(conn, rowid)
    content.run_content_batch(conn, content.StubContentEmbedder())
    stats = library_content.stats(conn)
    assert -1.0 <= stats["sim_lo"] < stats["sim_hi"] <= 1.0


def test_a_mixed_width_corpus_raises_rather_than_silently_misaligning(conn):
    """Reachable only via a model change mid-migration, but silently
    ignoring it would compute a mean over misaligned components and
    produce plausible-but-wrong scores -- the same class of corruption
    `reco.content_sim`'s own length check exists to catch."""
    insert_track(conn, 1)
    insert_track(conn, 2)
    insert_track(conn, 3)
    library_content.upsert(
        conn, 1, content_vec=content.pack_vector([1.0, 2.0]), model="m", now=1
    )
    library_content.upsert(
        conn, 2, content_vec=content.pack_vector([3.0, 4.0]), model="m", now=1
    )
    library_content.upsert(
        conn, 3, content_vec=content.pack_vector([5.0, 6.0, 7.0]), model="m", now=1
    )
    with pytest.raises(ValueError, match="track 3"):
        content.recompute_stats(conn)
