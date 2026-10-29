"""The real thing: muse driving universal_reco's CLI in its own virtualenv.

Marked slow, so `uv run pytest` skips it. Run explicitly with `-m slow`
after `uv sync --project universal_reco`. This is the only test in the muse
suite that depends on that virtualenv existing.
"""

from pathlib import Path

import pytest

from muse import content
from muse.library import content as library_content

FIXTURE = Path(__file__).resolve().parents[1] / (
    "universal_reco/tests/fixtures/sample.wav"
)

pytestmark = pytest.mark.slow


def test_the_real_embedder_produces_a_stored_vector(conn):
    conn.execute(
        "INSERT INTO tracks (track_rowid, origin, metadata_tier, artist_name,"
        " artist_names, album_name, track_name, file_path, status, added_at)"
        " VALUES (1, 'local', 'ytdlp', 'A', '[\"A\"]', 'Al', 'T', ?, 'done', 0)",
        (str(FIXTURE),),
    )
    conn.commit()

    result = content.run_content_batch(
        conn, content.SubprocessContentEmbedder()
    )

    assert result.embedded == 1
    assert result.failed == 0
    vector = library_content.vectors(conn)[1]
    assert len(vector) == content.CONTENT_DIM
    assert any(value != 0.0 for value in vector)


def test_the_real_embedder_reports_a_missing_file_as_a_failure(conn):
    """`embed()` raises loudly on unreadable audio by design; the batch CLI
    turns that into an error line, and the driver into a failed count -- the
    track stays on the queue rather than being stored as a zero vector."""
    conn.execute(
        "INSERT INTO tracks (track_rowid, origin, metadata_tier, artist_name,"
        " artist_names, album_name, track_name, file_path, status, added_at)"
        " VALUES (1, 'local', 'ytdlp', 'A', '[\"A\"]', 'Al', 'T',"
        " '/no/such/file.mp3', 'done', 0)"
    )
    conn.commit()

    result = content.run_content_batch(
        conn, content.SubprocessContentEmbedder()
    )

    assert result.embedded == 0
    assert result.failed == 1
    assert [row["track_rowid"] for row in library_content.pending(conn)] == [1]
