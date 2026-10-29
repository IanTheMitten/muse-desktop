"""LRCLIB: the one synced-lyrics provider (PRD "Synced lyrics"), and the
batch that runs it library-wide.

Three layers, deliberately separable for testing:

- `search_lrclib` is the only thing that touches the network — a thin
  wrapper around LRCLIB's keyless `/api/search`. Everything above it is
  pure and never constructs a `requests.Session` on its own.
- `pick_best` and `decide_outcome` turn a raw LRCLIB response into a
  decision: which `lyrics_state.state` to record and what text (if any)
  belongs in `.lrc`/`.lyrics.txt`. Neither touches disk or the database, so
  every branch of the state machine (O9, O10) is a plain function call in a
  test, no fake filesystem or fake connection required.
- `sync_batch` is the resumable driver: reads `lyrics_state.pending`, calls
  the two above per track, and writes the files. `muse/jobs.py` wraps it
  the same way it wraps `art.backfill` for the live API process; standalone
  runs go through `muse/lyrics_sync.py`.

The one hard invariant this module exists to protect: a track whose
`lyrics_state.state == 'manual'` is never even queried, because a listener's
hand-fixed lyrics or timings must survive every future automated pass. See
the guard in `sync_batch`.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import requests

import lyrics_quality
from .library import lyrics_state as library_lyrics_state
from .lrc import strip_timestamps
from .lyrics import lyrics_files
from .titles import normalize_title

log = logging.getLogger("muse.lyrics_lrclib")

LYRICS_SYNC = "lyrics_sync"

LRCLIB_SEARCH_URL = "https://lrclib.net/api/search"

# LRCLIB is keyless and asks for nothing, but a contactable identity is the
# same courtesy this project already extends to MusicBrainz (`muse/art.py`).
USER_AGENT = "muse_intelligence/1.0 ( slingstone7@gmail.com )"

DEFAULT_TIMEOUT = 15

# PRD: "a synced result preferred within ±5 s" — the tolerance a candidate's
# `duration` (LRCLIB, in seconds) must fall within of the track's own
# duration to be trusted as the same recording at all.
DURATION_TOLERANCE_SECONDS = 5


def search_lrclib(
    track_name: str,
    artist_name: str,
    album_name: str = "",
    *,
    timeout: float = DEFAULT_TIMEOUT,
    session: requests.Session | None = None,
) -> list[dict]:
    """`GET /api/search`, parsed. A transport failure (timeout, DNS, a 5xx)
    raises — the caller (`sync_batch`) is what decides a failed lookup is a
    per-track skip rather than the whole batch's problem, so this stays a
    thin, honest wrapper rather than swallowing the error itself.

    LRCLIB answers "nothing found" with a 404 on this endpoint, which is not
    a transport failure — it is the normal shape of a miss — so it comes
    back as an empty list like any other search with no results.
    """
    client = session or requests
    response = client.get(
        LRCLIB_SEARCH_URL,
        params={
            "track_name": track_name,
            "artist_name": artist_name,
            "album_name": album_name,
        },
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
    )
    if response.status_code == 404:
        return []
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, list) else []


def pick_best(results: list[dict], duration_ms: int | None) -> dict | None:
    """The LRCLIB candidate to trust, or None.

    Pure and I/O-free. Restricts to candidates within
    `DURATION_TOLERANCE_SECONDS` of `duration_ms`, then prefers one with
    non-empty `syncedLyrics` over one without, then the closest duration.
    `None` when `duration_ms` is unknown (nothing to verify a match against)
    or nothing qualifies.
    """
    if not results or not duration_ms:
        return None

    target = duration_ms / 1000
    candidates = [
        r for r in results
        if r.get("duration") is not None
        and abs(r["duration"] - target) <= DURATION_TOLERANCE_SECONDS
    ]
    if not candidates:
        return None

    def sort_key(candidate: dict) -> tuple[int, float]:
        has_sync = 0 if (candidate.get("syncedLyrics") or "").strip() else 1
        return has_sync, abs(candidate["duration"] - target)

    return min(candidates, key=sort_key)


def build_lrc(candidate: dict, track: dict) -> str:
    """The `.lrc` file to write: LRCLIB's `syncedLyrics` untouched (the
    timings are exactly what LRCLIB measured), prefixed with `[ar:]`/`[ti:]`/
    `[al:]` tags from the *track's own* metadata rather than LRCLIB's, so the
    file matches what the catalog says (story 100) if the two ever disagree
    on spelling. Purely for portability to another player; nothing here is
    read back by this app, which always goes through `syncedLyrics`/derived
    text or the stored `lrc_offset_ms`.
    """
    header = []
    if track.get("artist_name"):
        header.append(f"[ar:{track['artist_name']}]")
    if track.get("track_name"):
        header.append(f"[ti:{track['track_name']}]")
    if track.get("album_name"):
        header.append(f"[al:{track['album_name']}]")
    body = candidate.get("syncedLyrics") or ""
    return "\n".join([*header, body]) if header else body


@dataclass(frozen=True, slots=True)
class SyncOutcome:
    """One track's decided fate: the `lyrics_state` row to write, and the
    file contents (if any) to write alongside it. `None` on either text
    field means "leave that file exactly as it is" — not "write an empty
    file" — which is what lets the synced-but-invalid-derived-text branch
    write `.lrc` alone without touching a `.lyrics.txt` that might already
    hold something better.
    """

    state: str
    synced: bool
    lrc_text: str | None = None
    lyrics_text: str | None = None


def decide_outcome(track: dict, results: list[dict]) -> SyncOutcome:
    """The state machine O9/O10 specify, for one track.

    `track` needs `track_name`, `artist_name`, `duration_ms`, and
    `is_instrumental` (the filename-heuristic fallback from
    `muse.library.tracks.is_instrumental_path`, already on the row).
    `results` is exactly what `search_lrclib` returned — this function picks
    its own candidate via `pick_best` rather than taking one pre-picked, so
    a caller only has to pass through the raw response.

    Pure and I/O-free: no file is opened, no row is written. `sync_batch`
    is what turns a `SyncOutcome` into disk and database writes.
    """
    candidate = pick_best(results, track.get("duration_ms"))

    # O9: LRCLIB's explicit flag wins over the filename heuristic when it
    # has an opinion at all; the heuristic only gets a vote when LRCLIB
    # found nothing to ask.
    if candidate is not None and candidate.get("instrumental"):
        return SyncOutcome(state="declared_instrumental", synced=False)
    if candidate is None:
        if track.get("is_instrumental"):
            return SyncOutcome(state="declared_instrumental", synced=False)
        return SyncOutcome(state="not_found", synced=False)

    synced_text = (candidate.get("syncedLyrics") or "").strip()
    if synced_text:
        derived = strip_timestamps(synced_text)
        ok, _reason = lyrics_quality.validate(derived, track.get("track_name", ""))
        lrc_text = build_lrc(candidate, track)
        if ok:
            return SyncOutcome(
                state="synced", synced=True, lrc_text=lrc_text, lyrics_text=derived
            )
        # Defensive case: the timings are still trustworthy even when the
        # derived-text gate is being cautious, so the `.lrc` is kept and only
        # the `.lyrics.txt` write is withheld — never silently write text
        # that failed the same gate every other provider here respects.
        return SyncOutcome(state="synced", synced=True, lrc_text=lrc_text)

    plain_text = (candidate.get("plainLyrics") or "").strip()
    if plain_text:
        ok, _reason = lyrics_quality.validate(plain_text, track.get("track_name", ""))
        if ok:
            return SyncOutcome(state="plain", synced=False, lyrics_text=plain_text)
        # Failing validation means "found but not better than silence", not
        # "not found" — the existing file (if any) is left exactly alone.
        return SyncOutcome(state="plain", synced=False)

    # A candidate matched on duration but LRCLIB has no text at all for it —
    # a real gap in LRCLIB's own data, handled the same as no match found.
    return SyncOutcome(state="not_found", synced=False)


@dataclass(slots=True)
class SyncBatchResult:
    considered: int = 0
    synced: int = 0
    plain: int = 0
    declared_instrumental: int = 0
    not_found: int = 0
    failed: int = 0


_TALLY_FIELD = {
    "synced": "synced",
    "plain": "plain",
    "declared_instrumental": "declared_instrumental",
    "not_found": "not_found",
}


def _write_files(track: dict, outcome: SyncOutcome, lyrics_root: Path) -> Path | None:
    """Write whatever `outcome` says into `lyrics_root` (`<rowid>.lrc` /
    `<rowid>.txt`), never beside the audio, and return the `.txt` path
    written (or None) so the caller can point `tracks.lyrics_path` at it --
    the file on disk is invisible to `read_lyrics`/the embed batch until
    something does.
    """
    txt, lrc = lyrics_files(lyrics_root, track["track_rowid"])
    lyrics_root.mkdir(parents=True, exist_ok=True)
    if outcome.lrc_text is not None:
        lrc.write_text(outcome.lrc_text, encoding="utf-8")
    if outcome.lyrics_text is not None:
        txt.write_text(outcome.lyrics_text, encoding="utf-8")
        return txt
    return None


def _apply(
    conn, track: dict, outcome: SyncOutcome, *, checked_at: int, lyrics_root: Path
) -> None:
    """Turn one decided `SyncOutcome` into disk and database writes.

    Grouped into one function, called from inside the loop's own `try`, so
    a write failure partway through (a read-only mount, a path oddity in
    one of ~700 real filenames) is this one track's problem, not the rest
    of the batch's -- the same failure-isolation `run_embed_batch` and
    `art.backfill` already give their own per-item work.
    """
    lyrics_path = _write_files(track, outcome, lyrics_root)
    if lyrics_path is not None and not track.get("lyrics_path"):
        library_lyrics_state.set_lyrics_path_if_absent(
            conn, track["track_rowid"], str(lyrics_path)
        )
    library_lyrics_state.upsert(
        conn,
        track["track_rowid"],
        state=outcome.state,
        synced=outcome.synced,
        checked_at=checked_at,
    )


def sync_batch(
    conn,
    lrclib_search: Callable[..., list[dict]] = search_lrclib,
    *,
    lyrics_root: Path,
    rate_limit_seconds: float = 0.35,
    sleep: Callable[[float], None] = time.sleep,
    on_progress: Callable[[int, int, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> SyncBatchResult:
    """Sync-fetch every playable track LRCLIB has not been asked about yet.

    Resumable by construction: the work queue is
    `library.lyrics_state.pending`, which is exactly "no row yet" — a track
    already decided (including `manual`) is never revisited by this pass, so
    a killed run restarts into only what it had not reached.

    Blocking on purpose, like `art.backfill`: it spends its life waiting on
    `rate_limit_seconds` between requests, so the caller (`muse/jobs.py`)
    runs it on a worker thread.
    """
    result = SyncBatchResult()
    queue = library_lyrics_state.pending(conn)
    result.considered = len(queue)
    checked_at = int(time.time())

    for done, track in enumerate(queue, start=1):
        if should_stop is not None and should_stop():
            break

        # Belt-and-suspenders on top of `pending()` already excluding any
        # track with an existing row: a bug in that query must never turn
        # into LRCLIB traffic for a track someone corrected by hand — the
        # one invariant this whole module exists to protect.
        if library_lyrics_state.get_state(conn, track["track_rowid"]) == "manual":
            continue

        try:
            query_name = normalize_title(track["track_name"]) or track["track_name"]
            results = lrclib_search(
                track_name=query_name,
                artist_name=track["artist_name"],
                album_name=track.get("album_name") or "",
            )
            # Deciding and applying the outcome stay inside the same guard as
            # the search itself: a malformed LRCLIB response (a `duration`
            # that arrives as a string) or a write failure on one real
            # filename must cost this one track, not every track still
            # queued behind it.
            outcome = decide_outcome(track, results)
            _apply(
                conn, track, outcome, checked_at=checked_at, lyrics_root=lyrics_root
            )
        except Exception:
            log.exception(
                "Lyrics sync failed for track %d", track["track_rowid"]
            )
            result.failed += 1
            sleep(rate_limit_seconds)
            if on_progress is not None:
                on_progress(done, len(queue), f"track {track['track_rowid']} (failed)")
            continue

        field = _TALLY_FIELD.get(outcome.state)
        if field is not None:
            setattr(result, field, getattr(result, field) + 1)

        if on_progress is not None:
            on_progress(done, len(queue), f"track {track['track_rowid']}")
        sleep(rate_limit_seconds)

    return result
