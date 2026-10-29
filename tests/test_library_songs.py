from muse.library import queries
from tests.conftest import insert_track


def test_songs_title_order_and_statuses(conn):
    insert_track(conn, 1, track_name="banana")
    insert_track(conn, 2, track_name="Apple")
    insert_track(conn, 3, track_name="cherry", status="missing_file")
    insert_track(conn, 4, track_name="Aardvark", status="pending")

    out = queries.songs(conn)

    assert [t["track_name"] for t in out] == ["Apple", "banana", "cherry"]
    assert [t["playable"] for t in out] == [True, True, False]
