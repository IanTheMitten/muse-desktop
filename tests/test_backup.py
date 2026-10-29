"""Snapshots and retention.

A backup that does not run is a loud failure — `systemctl status` says so. The
failures worth testing are the ones that leave you with a directory full of
files that look like backups:

- pruning the wrong end of the list, which deletes thirty nights of history and
  keeps today's;
- pruning anything that is not a snapshot;
- a snapshot that opens fine and is missing the writes it was taken to keep;
- an rsync that quietly reaches the 47 GB of mirrors.

None of these announce themselves. All of them are discovered on the one day
the backup is needed.
"""

import sqlite3

import pytest
from conftest import FakeCatalog, make_manifest, make_record

from muse.backup import (
    PATTERN,
    prune,
    rsync_command,
    snapshot_name,
    snapshots,
    split_target,
    take_snapshot,
)
from muse.library import import_manifest, open_span


def make_snapshots(directory, *days):
    directory.mkdir(parents=True, exist_ok=True)
    made = []
    for day in days:
        path = directory / f"library-2026-01-{day:02d}.sqlite3"
        path.write_bytes(b"snapshot")
        made.append(path)
    return made


# --- the snapshot itself --------------------------------------------------


def test_a_snapshot_holds_the_history(conn, tmp_path):
    """The whole point. A snapshot that opens and has no `plays` in it is worse
    than no snapshot, because it is trusted."""
    import_manifest(make_manifest(make_record(1)), FakeCatalog(), conn)
    open_span(conn, track_rowid=1, duration_ms=180_000)

    db = tmp_path / "source.sqlite3"
    conn.execute("VACUUM INTO ?", (str(db),))

    out = take_snapshot(db, tmp_path / "backups")

    restored = sqlite3.connect(out)
    assert restored.execute("SELECT COUNT(*) FROM plays").fetchone()[0] == 1
    assert restored.execute("SELECT COUNT(*) FROM tracks").fetchone()[0] == 1
    restored.close()


def test_a_snapshot_is_one_file_with_no_sidecar(conn, tmp_path):
    """WAL mode means the live database is three files. A backup that needed
    all three would be restored wrong by anyone copying "the database"."""
    db = tmp_path / "source.sqlite3"
    conn.execute("VACUUM INTO ?", (str(db),))

    take_snapshot(db, tmp_path / "backups")

    assert [p.name for p in (tmp_path / "backups").iterdir()] == [snapshot_name()]


def test_a_second_run_the_same_day_replaces_rather_than_failing(conn, tmp_path):
    """`VACUUM INTO` refuses to overwrite. Unhandled, that turns a manual run
    into a failed timer for the rest of the day."""
    db = tmp_path / "source.sqlite3"
    conn.execute("VACUUM INTO ?", (str(db),))
    out = tmp_path / "backups"

    first = take_snapshot(db, out)
    second = take_snapshot(db, out)

    assert first == second
    assert list(out.glob("*.part")) == []


def test_snapshot_names_sort_chronologically():
    """Retention is a sort. A name that sorts differently from time is how the
    oldest snapshot survives and the newest gets deleted."""
    from datetime import date

    names = [
        snapshot_name(date(2026, 1, 9)),
        snapshot_name(date(2026, 1, 10)),
        snapshot_name(date(2025, 12, 31)),
    ]
    assert sorted(names) == [
        snapshot_name(date(2025, 12, 31)),
        snapshot_name(date(2026, 1, 9)),
        snapshot_name(date(2026, 1, 10)),
    ]


# --- retention ------------------------------------------------------------


def test_pruning_keeps_the_newest(tmp_path):
    """The direction of this comparison is the difference between a month of
    history and one night of it."""
    out = tmp_path / "backups"
    make_snapshots(out, *range(1, 8))

    removed = prune(out, keep=3)

    assert [p.name for p in removed] == [
        "library-2026-01-01.sqlite3",
        "library-2026-01-02.sqlite3",
        "library-2026-01-03.sqlite3",
        "library-2026-01-04.sqlite3",
    ]
    assert [p.name for p in snapshots(out)] == [
        "library-2026-01-05.sqlite3",
        "library-2026-01-06.sqlite3",
        "library-2026-01-07.sqlite3",
    ]


def test_pruning_under_the_limit_removes_nothing(tmp_path):
    out = tmp_path / "backups"
    make_snapshots(out, 1, 2)
    assert prune(out, keep=30) == []
    assert len(snapshots(out)) == 2


def test_pruning_never_touches_anything_that_is_not_a_snapshot(tmp_path):
    """This code deletes files. Everything it can see must be something it
    made."""
    out = tmp_path / "backups"
    make_snapshots(out, *range(1, 6))
    bystander = out / "library.sqlite3"
    bystander.write_bytes(b"the live database, somehow")
    notes = out / "notes.txt"
    notes.write_text("do not delete")

    prune(out, keep=1)

    assert bystander.exists()
    assert notes.exists()


def test_pruning_to_nothing_is_refused(tmp_path):
    out = tmp_path / "backups"
    make_snapshots(out, 1, 2)
    with pytest.raises(ValueError):
        prune(out, keep=0)
    assert len(snapshots(out)) == 2


def test_an_empty_directory_is_not_an_error(tmp_path):
    assert snapshots(tmp_path / "nothing-here") == []
    assert prune(tmp_path / "nothing-here") == []


# --- the push -------------------------------------------------------------


def test_the_push_names_only_the_snapshot_directory(tmp_path):
    """47 GB of mirrors and 7.2 GB of audio are excluded by never being
    reachable, not by an --exclude that could be edited wrong."""
    command = rsync_command(tmp_path / "backups", "ian@mac:muse-backups/")

    assert command[0] == "rsync"
    assert command[-2] == f"{(tmp_path / 'backups').as_posix()}/"
    assert command[-1] == "ian@mac:muse-backups/"
    assert not any("MUSIC" in part or "spotify_clean" in part for part in command)


def test_the_push_sends_only_finished_snapshots(tmp_path):
    """A `.part` file caught mid-write would arrive on the other machine
    looking like a backup."""
    command = rsync_command(tmp_path, "mac:backups/")
    assert f"--include={PATTERN}" in command
    assert "--exclude=*" in command
    assert command.index(f"--include={PATTERN}") < command.index("--exclude=*")


def test_the_push_is_argv_not_a_shell_string(tmp_path):
    """A directory with a space in it must stay one argument."""
    spaced = tmp_path / "my backups"
    command = rsync_command(spaced, "mac:b/")
    assert f"{spaced.as_posix()}/" in command


@pytest.mark.parametrize(
    "target,expected",
    [
        ("ian@mac:backups/", ("ian@mac", "backups/")),
        ("mac:backups/", ("mac", "backups/")),
        ("mac", ("mac", "")),
        ("ian@mac:/abs/path", ("ian@mac", "/abs/path")),
    ],
)
def test_the_target_splits_host_from_path(target, expected):
    assert split_target(target) == expected
