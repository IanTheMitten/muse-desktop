"""The real thing: the ML worker embedding one clip with MERT.

Marked slow, so `uv run pytest` skips it. Run explicitly with
`uv run --extra embed pytest -m slow tests/test_content_slow.py`.
"""

import sys
from pathlib import Path

import pytest

from muse import content, content_embed_batch
from muse.library import content as library_content
from muse.worker import WorkerClient, WorkerContentEmbedder

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample.wav"

pytestmark = pytest.mark.slow


def test_the_worker_embeds_a_clip_into_a_stored_vector(conn, tmp_path):
    conn.execute(
        "INSERT INTO tracks (track_rowid, origin, metadata_tier, artist_name,"
        " artist_names, album_name, track_name, file_path, status, added_at)"
        " VALUES (1, 'local', 'ytdlp', 'A', '[\"A\"]', 'Al', 'T', ?, 'done', 0)",
        (str(FIXTURE),),
    )
    conn.commit()

    client = WorkerClient(
        [sys.executable, "-m", "muse.worker_main", "--weights", str(tmp_path)]
    )
    try:
        content_embed_batch.run(conn, WorkerContentEmbedder(client))
    finally:
        client.stop()

    vectors = library_content.vectors(conn)
    assert list(vectors) == [1]
    assert len(vectors[1]) == content.CONTENT_DIM
