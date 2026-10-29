"""The lyrics-changed pass looks at every already-embedded track on every
run (to catch a manual edit), so it must not cost one SQL round-trip per
track -- that turns a normal, nothing-changed startup into an O(library)
query storm for no reason.
"""

from muse import embed
from tests.conftest import insert_track


def _with_lyrics(conn, tmp_path, rowid, text):
    insert_track(conn, rowid, status="done")
    path = tmp_path / f"{rowid}.lyrics.txt"
    path.write_text(text, encoding="utf-8")
    conn.execute(
        "UPDATE tracks SET lyrics_path = ?, file_path = 'MUSIC/x.m4a'"
        " WHERE track_rowid = ?",
        (str(path), rowid),
    )


def test_the_lyrics_changed_pass_does_not_query_once_per_track(conn, tmp_path):
    for rowid in range(1, 11):
        _with_lyrics(conn, tmp_path, rowid, f"Lyrics for track {rowid}")
    embed.run_embed_batch(conn, embed.StubAudioEncoder(), embed.StubLyricsEncoder())

    real_execute = type(conn).execute
    lyrics_path_lookups = {"n": 0}

    def counting_execute(self, sql, parameters=()):
        if "SELECT lyrics_path FROM tracks WHERE track_rowid" in sql:
            lyrics_path_lookups["n"] += 1
        return real_execute(self, sql, parameters)

    conn_type = type(conn)
    original = conn_type.execute
    conn_type.execute = counting_execute
    try:
        embed.run_embed_batch(conn, embed.StubAudioEncoder(), embed.StubLyricsEncoder())
    finally:
        conn_type.execute = original

    # However this pass fetches lyrics_path, it must not be one query per
    # already-embedded track (10 tracks -> at most a small constant number
    # of round trips, not 10).
    assert lyrics_path_lookups["n"] <= 1
