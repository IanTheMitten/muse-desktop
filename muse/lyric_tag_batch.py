"""Library-wide lyric tagging: `python -m muse.lyric_tag_batch`.

`docs/specs/2026-10-12-lyric-tag-store.md` D4-D6. Standalone -- a oneshot,
never inside the API process -- with job-row bookkeeping and an advisory
lock. Each pending track's lyrics are segmented and asked against taxonomy v3
(`muse/lyric_tags.py`); tags come from Laya in the ML worker
(`muse/worker.py`), and the raw probabilities go to
`lyric_tag_sets`/`lyric_tags`.

Before tagging, sets tagged from prefetched lyrics whose audio has since
landed are linked to their track when the lyrics match, so those songs cost
no request (D4).

`--import DIR` loads the tag files written by the 2026-10-12 prototype run
(`~/muse-expansion/tags/`) instead of asking anything (D6).
"""

import argparse
try:
    import fcntl
except ImportError:  # shortcut: Windows has no flock, so the batch lock is a no-op there; upgrade to msvcrt.locking if a second writer ever appears
    fcntl = None
import json
import logging
import os
import sys
from contextlib import contextmanager
from pathlib import Path

from . import library
from . import lyric_tags as tags_
from .config import Config
from .library import jobs as job_rows
from .library import lyric_tags as store
from .lyrics import read_lyrics
from .worker import LAYA_MODEL, WorkerClient, worker_ask, worker_command

log = logging.getLogger("muse.lyric_tag_batch")

LYRIC_TAG = "lyric_tag"
LOCK_HELD_EXIT = 3  # another batch holds the lock
# One bad song is logged and retried next run; this many in a row is an
# outage (worker down), and the run stops rather than burn the queue.
MAX_CONSECUTIVE_FAILURES = 10


def lock_path(library_db: Path) -> Path:
    return library_db.with_name(library_db.name + ".lyric_tag_batch.lock")


@contextmanager
def exclusive(library_db: Path):
    """Hold the batch lock (an exclusive `flock` on `lock_path`) for the body,
    or yield False if another run has it. A lock, not only
    `abandon_running_of_kind`, so a second run cannot reap the first run's
    live job row."""
    fd = os.open(lock_path(library_db), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        if fcntl:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        log.info("Lyric tag batch already running (lock held at %s)", lock_path(library_db))
        yield False
        return
    except OSError:
        os.close(fd)
        raise
    try:
        yield True
    finally:
        if fcntl:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def track_lines(lyrics_path: str | None) -> list[tags_.Line]:
    """A track's cleaned lyric lines: from the sibling `.lrc` when there is
    one (it carries the timestamps the thirds are cut by), else the plain
    text. The same source order the prototype used, so `text_hash` agrees
    with its files."""
    if not lyrics_path:
        return []
    path = Path(lyrics_path)
    if path.name.endswith(".lyrics.txt"):
        lrc = path.with_name(path.name.removesuffix(".lyrics.txt") + ".lrc")
        if lrc.exists():
            return tags_.lines_from_lrc(lrc.read_text(encoding="utf-8", errors="replace"))
    return tags_.lines_from_text(read_lyrics(lyrics_path) or "")


def _embeddings_hash(conn, track_rowid: int) -> str | None:
    row = conn.execute(
        "SELECT lyrics_hash FROM embeddings WHERE track_rowid = ?", (track_rowid,)
    ).fetchone()
    return row["lyrics_hash"] if row else None


def link_pending(conn) -> dict:
    """Link each unlinked set whose song has landed, when the track's lyrics
    are the ones that were tagged; drop it when they are not, leaving the
    track pending (spec D4)."""
    linked = dropped = 0
    for c in store.link_candidates(conn):
        if tags_.text_hash(track_lines(c["lyrics_path"])) == c["text_hash"]:
            store.attach(conn, c["set_id"], c["track_rowid"], c["e_lyrics_hash"])
            linked += 1
        else:
            store.delete_set(conn, c["set_id"])
            dropped += 1
    return {"linked": linked, "dropped": dropped}


def _song_name(conn, track_rowid: int) -> tuple[str, str | None, str | None]:
    row = conn.execute(
        "SELECT artist_name, track_name, lyrics_path, recording_mbid FROM tracks"
        " WHERE track_rowid = ?", (track_rowid,)
    ).fetchone()
    return f"{row['artist_name']} — {row['track_name']}", row["lyrics_path"], row["recording_mbid"]


def run(conn, ask, model: str) -> dict:
    """Link what landed, then tag every pending track; recorded as a
    `lyric_tag` job. Commits per track, so a killed run keeps its work."""
    job_rows.abandon_running_of_kind(conn, LYRIC_TAG)
    links = link_pending(conn)
    queue = store.pending(conn, tags_.TAXONOMY_VERSION)
    job_id = job_rows.create(conn, LYRIC_TAG, {"tracks": len(queue)})
    job_rows.update(conn, job_id, state=job_rows.RUNNING,
                    message=f"Tagging {len(queue)} track(s)")

    tagged = no_lines = failed = consecutive = 0
    total = len(queue)
    for done, track_rowid in enumerate(queue, start=1):
        song, lyrics_path, mbid = _song_name(conn, track_rowid)
        lines = track_lines(lyrics_path)
        common = dict(track_rowid=track_rowid, recording_mbid=mbid,
                      text_hash=tags_.text_hash(lines),
                      lyrics_hash=_embeddings_hash(conn, track_rowid),
                      model=model, taxonomy_version=tags_.TAXONOMY_VERSION)
        if not tags_.segments(lines):
            store.write_set(conn, state="no_lines", tags={}, **common)
            no_lines += 1
        else:
            try:
                result = tags_.tag_song(ask, song, lines)
            except Exception as error:  # one bad song must not end the batch
                failed += 1
                consecutive += 1
                log.warning("Lyric tags failed for track %s (%s): %s",
                            track_rowid, song, error)
                if consecutive > MAX_CONSECUTIVE_FAILURES:
                    message = (f"stopped after {consecutive} failures in a row"
                               f" ({tagged} tagged first): {error}")
                    job_rows.update(conn, job_id, state=job_rows.ERROR, message=message)
                    log.error("Lyric tag batch: %s", message)
                    return job_rows.get(conn, job_id)
                continue
            store.write_set(conn, state="tagged", tags=result, **common)
            tagged += 1
            consecutive = 0
        job_rows.update(conn, job_id, progress=done / total, message=f"track {track_rowid}")
        if done == total or done % max(50, total // 100) == 0:
            log.info("Lyric tag batch: %d of %d", done, total)

    summary = (f"{tagged} tagged, {no_lines} without lyric lines, {failed} failed,"
               f" {links['linked']} linked, {total} considered")
    job_rows.update(conn, job_id, state=job_rows.DONE, progress=1.0, message=summary)
    log.info("Lyric tag batch: %s", summary)
    return job_rows.get(conn, job_id)


def import_dir(conn, directory: Path, model: str = "jev-latest") -> dict:
    """Load prototype tag files (spec D6). `lib-<rowid>.json` is stored
    linked only when the track's current lyrics are the ones tagged;
    `mb-<mbid>.json` is held unlinked until its song lands. Idempotent."""
    imported = skipped = 0
    for path in sorted(Path(directory).glob("*.json")):
        data = json.loads(path.read_text())
        if data.get("taxonomy_version") != tags_.TAXONOMY_VERSION or data.get("state") not in (
                "tagged", "no_lines"):
            skipped += 1
            continue
        result = {} if data["state"] == "no_lines" else {
            "song": data["song_level"], **data["answers"]}
        common = dict(state=data["state"], text_hash=data["lyrics_hash"], model=model,
                      tags=result, taxonomy_version=tags_.TAXONOMY_VERSION)
        key = path.stem
        if key.startswith("lib-"):
            track_rowid = int(key.removeprefix("lib-"))
            row = conn.execute(
                "SELECT lyrics_path, recording_mbid FROM tracks WHERE track_rowid = ?",
                (track_rowid,)).fetchone()
            current = store.find_set(conn, track_rowid=track_rowid)
            if (row is None
                    or tags_.text_hash(track_lines(row["lyrics_path"])) != data["lyrics_hash"]
                    or (current is not None and current["text_hash"] == data["lyrics_hash"]
                        and current["taxonomy_version"] == tags_.TAXONOMY_VERSION)):
                skipped += 1
                continue
            store.write_set(conn, track_rowid=track_rowid, recording_mbid=row["recording_mbid"],
                            lyrics_hash=_embeddings_hash(conn, track_rowid), **common)
        elif key.startswith("mb-"):
            mbid = key.removeprefix("mb-")
            if conn.execute(
                "SELECT 1 FROM lyric_tag_sets WHERE recording_mbid = ?"
                " AND (track_rowid IS NOT NULL OR text_hash = ?)",
                (mbid, data["lyrics_hash"]),
            ).fetchone():
                skipped += 1
                continue
            store.write_set(conn, track_rowid=None, recording_mbid=mbid, lyrics_hash=None, **common)
        else:
            skipped += 1
            continue
        imported += 1
    return {"imported": imported, "skipped": skipped, **link_pending(conn)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Library-wide lyric tagging (taxonomy v3)")
    parser.add_argument("--import", dest="import_dir", type=Path, metavar="DIR",
                        help="load prototype tag files instead of asking the worker")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
                        datefmt="%H:%M:%S")
    config = Config.from_env()
    if not config.library_db.exists():
        log.error("No library at %s", config.library_db)
        return 1

    if args.import_dir is not None:
        conn = library.connect(config.library_db)
        try:
            counts = import_dir(conn, args.import_dir)
        finally:
            conn.close()
        log.info("Imported: %s", counts)
        print(json.dumps(counts))
        return 0

    with exclusive(config.library_db) as acquired:
        if not acquired:
            return LOCK_HELD_EXIT
        conn = library.connect(config.library_db)
        client = WorkerClient(worker_command(config.models_dir, config.worker_cmd))
        try:
            job = run(conn, worker_ask(client), LAYA_MODEL)
        finally:
            client.stop()
            conn.close()
    return 0 if job["state"] == job_rows.DONE else 2


if __name__ == "__main__":
    sys.exit(main())
