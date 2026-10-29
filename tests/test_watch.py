import asyncio

from muse.watch import watch_folders


def _run(body):
    async def main():
        stop = asyncio.Event()
        hits = []

        async def on_change():
            hits.append(1)

        task = asyncio.create_task(
            watch_folders(body.folders, on_change, stop=stop, poll_seconds=0.5)
        )
        await asyncio.sleep(0.3)
        await body.act()
        for _ in range(100):
            if hits:
                break
            await asyncio.sleep(0.1)
        stop.set()
        await asyncio.wait_for(task, 5)
        return hits

    return asyncio.run(asyncio.wait_for(main(), 10))


def test_file_write_calls_back(tmp_path):
    class B:
        folders = [tmp_path]

        async def act(self):
            (tmp_path / "a.txt").write_text("x")

    assert _run(B())


def test_root_created_after_start(tmp_path):
    root = tmp_path / "later"

    class B:
        folders = [root]

        async def act(self):
            root.mkdir()

    assert _run(B())


def test_stop_ends(tmp_path):
    async def main():
        stop = asyncio.Event()

        async def cb():
            pass

        task = asyncio.create_task(
            watch_folders([tmp_path], cb, stop=stop, poll_seconds=0.5)
        )
        await asyncio.sleep(0.2)
        stop.set()
        await asyncio.wait_for(task, 5)

    asyncio.run(asyncio.wait_for(main(), 10))
