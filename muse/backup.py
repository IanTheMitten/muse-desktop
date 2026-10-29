"""Nightly snapshots of the one irreplaceable thing here.

`plays` is the training set for every slice after this one. It is also the only
data in the system that cannot be rebuilt: the audio is yt-dlp again, the
catalog mirrors are a public scrape again, and the manifest is in git. So the
backup is small, and it is the whole point.

Two decisions carry this module:

**`VACUUM INTO`, not a file copy.** The database runs in WAL mode and is written
while the snapshot is taken. A copy of the main file mid-write is not a
consistent database — it is a file that opens fine and is missing the most
recent writes, or worse. `VACUUM INTO` asks SQLite for a consistent snapshot and
gets one, in one file, with no `-wal` sidecar to remember to copy.

**Audio and the mirrors are never in scope.** 47 GB of catalog and 7.2 GB of
audio are excluded not to save space but because including them would make the
backup something that fails, gets ignored, and is not there when it matters.

Run it directly: `python -m muse.backup`.
"""

import argparse
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

from .config import Config

log = logging.getLogger("muse.backup")

# Dated, and named so a plain lexical sort is also a chronological one.
PREFIX = "library-"
SUFFIX = ".sqlite3"
PATTERN = f"{PREFIX}*{SUFFIX}"

# A month of nightly snapshots. Long enough that a corruption noticed late is
# still recoverable, small enough that thirty copies of a few megabytes is
# nothing.
KEEP = 30


def snapshot_name(when: date | None = None) -> str:
    return f"{PREFIX}{(when or date.today()).isoformat()}{SUFFIX}"


def take_snapshot(
    library_db: Path, out_dir: Path, *, when: date | None = None
) -> Path:
    """A consistent copy of the library, written atomically.

    `VACUUM INTO` refuses to overwrite, so a second run on the same day writes
    beside the first and replaces it only once it is complete. A snapshot that
    is half-written is worse than no snapshot: it is a file that looks like a
    backup.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    final = out_dir / snapshot_name(when)
    staging = out_dir / f".{final.name}.{os.getpid()}.part"
    staging.unlink(missing_ok=True)

    conn = sqlite3.connect(f"file:{library_db.as_posix()}?mode=ro", uri=True)
    try:
        conn.execute("VACUUM INTO ?", (str(staging),))
    finally:
        conn.close()

    staging.replace(final)
    return final


def snapshots(out_dir: Path) -> list[Path]:
    """Every snapshot, oldest first.

    Scoped to the naming pattern on purpose. Anything else in the directory is
    somebody else's file and is not this module's to enumerate — or to delete.
    """
    if not out_dir.exists():
        return []
    return sorted(out_dir.glob(PATTERN))


def prune(out_dir: Path, keep: int = KEEP) -> list[Path]:
    """Delete the oldest snapshots beyond `keep`. Returns what was removed."""
    if keep < 1:
        raise ValueError("keep must be at least 1")
    existing = snapshots(out_dir)
    doomed = existing[: max(len(existing) - keep, 0)]
    for path in doomed:
        path.unlink()
    return doomed


def rsync_command(out_dir: Path, target: str) -> list[str]:
    """The push, as argv.

    Built as a list and never as a shell string: the target is configuration
    and a path with a space in it must not become two arguments.

    Only the snapshot directory is ever named. There is no path here that could
    reach `MUSIC/` or a mirror even if the excludes were wrong.
    """
    return [
        "rsync",
        "--archive",
        "--delete",  # the second machine mirrors retention rather than growing forever
        f"--include={PATTERN}",
        "--exclude=*",  # everything else, including the .part files of a run in flight
        f"{out_dir.as_posix()}/",
        target,
    ]


def resolve_host(host: str) -> str:
    """A tailnet hostname to an address Tailscale agrees with.

    MagicDNS does not resolve on this machine — `/etc/resolv.conf` carries no
    Tailscale route — so `ssh ians-macbook-pro` fails here while working fine
    from the phone. Asking `tailscale` directly is the fix that does not depend
    on the resolver being configured.
    """
    if not host or host[0].isdigit():
        return host
    try:
        found = subprocess.run(
            ["tailscale", "ip", "-4", host],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return host
    address = found.stdout.strip().splitlines()
    return address[0] if found.returncode == 0 and address else host


def split_target(target: str) -> tuple[str, str]:
    """`user@host:path` into the host part and the rest, so the host can be
    resolved without disturbing the path."""
    if ":" not in target:
        return target, ""
    left, path = target.split(":", 1)
    return left, path


def push(out_dir: Path, target: str, *, dry_run: bool = False) -> bool:
    """Send the snapshots to the second machine. False on any failure.

    A failed push is loud in the log and does not fail the run: the local
    snapshot has already been taken, and losing tonight's *copy* is a much
    smaller problem than skipping tonight's snapshot because a laptop was shut.
    """
    left, path = split_target(target)
    user, _, host = left.rpartition("@")
    resolved = resolve_host(host)
    remote = f"{user}@{resolved}" if user else resolved
    command = rsync_command(out_dir, f"{remote}:{path}" if path else remote)

    if dry_run:
        log.info("Would run: %s", " ".join(command))
        return True

    if shutil.which("rsync") is None:
        log.error("rsync is not installed; snapshots stay local only")
        return False

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        log.error(
            "Backup push to %s failed (%d): %s",
            target,
            result.returncode,
            (result.stderr or "").strip()[:400],
        )
        return False
    log.info("Pushed snapshots to %s", target)
    return True


def run(config: Config, *, keep: int = KEEP, dry_run: bool = False) -> dict:
    started = time.time()
    snapshot = take_snapshot(config.library_db, config.backup_dir)
    removed = prune(config.backup_dir, keep)

    pushed = None
    if config.backup_target:
        pushed = push(config.backup_dir, config.backup_target, dry_run=dry_run)
    else:
        log.info("No MUSE_BACKUP_TARGET set; snapshots stay on this machine")

    result = {
        "snapshot": str(snapshot),
        "bytes": snapshot.stat().st_size,
        "kept": len(snapshots(config.backup_dir)),
        "pruned": [path.name for path in removed],
        "pushed": pushed,
        "seconds": round(time.time() - started, 2),
    }
    log.info(
        "Snapshot %s (%.1f MB), %d kept, %d pruned, push=%s",
        snapshot.name,
        result["bytes"] / 1e6,
        result["kept"],
        len(removed),
        pushed,
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Snapshot and back up library.sqlite3")
    parser.add_argument("--keep", type=int, default=KEEP)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="take the snapshot but only print the push",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    config = Config.from_env()
    if not config.library_db.exists():
        log.error("No library at %s; nothing to back up", config.library_db)
        return 1

    result = run(config, keep=args.keep, dry_run=args.dry_run)
    # A push that failed is worth a non-zero exit so `systemctl status` shows
    # it, even though the snapshot itself succeeded.
    return 0 if result["pushed"] is not False else 2


if __name__ == "__main__":
    sys.exit(main())
