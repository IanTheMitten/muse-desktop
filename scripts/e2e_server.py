"""Fresh Muse on fixture tracks with a fake chart, for the Playwright run."""

import json
import os
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mutagen.flac import FLAC
from mutagen.id3 import ID3, TALB, TIT2, TPE1, TRCK
from mutagen.mp3 import MP3
from mutagen.wave import WAVE

ROOT = Path(__file__).resolve().parent.parent
SMOKE = ROOT / "tests" / "fixtures" / "smoke"
CHART = {"feed": {"results": [
    {"name": "First Light", "artistName": "Ada Arc", "url": "https://music.apple.com/x"},
    {"name": "Not Owned Song", "artistName": "Nobody", "url": "https://music.apple.com/y"},
]}}


def tag(path: Path, title: str, artist: str, album: str, n: int) -> None:
    ext = path.suffix
    if ext == ".flac":
        f = FLAC(path)
        f.update(title=title, artist=artist, album=album, tracknumber=str(n))
        f.save()
        return
    f = MP3(path) if ext == ".mp3" else WAVE(path)
    if f.tags is None:
        f.add_tags()
    f.tags.add(TIT2(encoding=3, text=title))
    f.tags.add(TPE1(encoding=3, text=artist))
    f.tags.add(TALB(encoding=3, text=album))
    f.tags.add(TRCK(encoding=3, text=str(n)))
    f.save()


def make_music(music: Path) -> None:
    exts = [".mp3", ".flac", ".wav"]
    names = ["Ada Arc", "Bo Bell", "Cy Crest"]
    i = 0
    for a, artist in enumerate(names):
        for b in range(2):
            album = f"Album {a}{b}"
            for n in (1, 2):
                title = "First Light" if i == 0 else f"Song {i:02d}"
                ext = exts[i % 3]
                d = music / artist / album
                d.mkdir(parents=True, exist_ok=True)
                dest = d / f"{n:02d} - {title}{ext}"
                shutil.copy(SMOKE / f"tone{ext}", dest)
                tag(dest, title, artist, album, n)
                i += 1


class Chart(BaseHTTPRequestHandler):
    def do_GET(self):
        ok = self.path.split("?")[0] == "/api/v2/us/music/most-played/10/songs.json"
        body = json.dumps(CHART).encode() if ok else b"{}"
        self.send_response(200 if ok else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="muse-e2e-"))
    music = tmp / "music"
    make_music(music)
    data = tmp / "data"
    data.mkdir()
    (data / "settings.json").write_text(json.dumps(
        {"music_folders": [str(music)], "music_files": [], "downloader_unlocked": False}))
    port = int(os.environ.get("E2E_PORT", "8765"))
    server = ThreadingHTTPServer(("127.0.0.1", port + 1), Chart)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    os.environ.update({
        "MUSE_DATA_DIR": str(data), "MUSE_PORT": str(port), "MUSE_TOKEN": "e2e-token",
        "MUSE_CHART_URL": f"http://127.0.0.1:{port + 1}",
        "MUSE_YTDLP_URL": f"http://127.0.0.1:{port + 1}/ytdlp",
        "MUSE_ART_BACKFILL": "0", "MUSE_LYRICS_SYNC": "0", "MUSE_TASTE_RETRAIN": "0",
        "MUSE_UPDATE_CHECK": "0", "MUSE_WEB_DIST": str(ROOT / "web" / "dist"),
    })
    sys.path.insert(0, str(ROOT))
    try:
        from muse.__main__ import main as muse_main
        muse_main()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
