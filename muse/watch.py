"""Watch the library folders with OS events, and notice roots coming and going."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

import watchfiles

log = logging.getLogger("muse.watch")

_default = watchfiles.DefaultFilter()


def _filter(change, path: str) -> bool:
    return _default(change, path) and ".incoming" not in Path(path).parts


async def watch_folders(
    folders: list[Path],
    on_change: Callable[[], Awaitable[None]],
    *,
    stop: asyncio.Event,
    poll_seconds: float = 60.0,
) -> None:
    """Call `on_change` on file changes and when a root appears or vanishes.

    The OS watcher cannot see a root that did not exist when it started, so
    root presence is re-checked every `poll_seconds`.
    """

    async def notify() -> None:
        try:
            await on_change()
        except Exception:
            log.exception("Watch callback failed")

    def present() -> list[Path]:
        return sorted(f for f in folders if f.is_dir())

    previous = present()
    while not stop.is_set():
        roots = present()
        if roots != previous:
            await notify()
            previous = roots
        if not roots:
            try:
                await asyncio.wait_for(stop.wait(), poll_seconds)
            except TimeoutError:
                pass
            continue
        async for changes in watchfiles.awatch(
            *roots,
            stop_event=stop,
            watch_filter=_filter,
            rust_timeout=int(poll_seconds * 1000),
            yield_on_timeout=True,
        ):
            if changes:
                await notify()
            if present() != roots:
                break
