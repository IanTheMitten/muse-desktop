"""Screenshots and the demo GIF for the README, from a freely licensed demo library.

    uv run python scripts/readme_shots.py              # download, serve, capture
    uv run python scripts/readme_shots.py --data DIR   # only build a data dir (for the desktop app)

The music and covers listed in docs/readme/demo-library.json are downloaded once
into a cache dir, tagged (cover embedded) and given any .lrc in
docs/readme/lyrics/. A fresh Muse serves them and web/scripts/readme-shots.mjs
drives Playwright over it, writing PNGs and demo.gif into docs/readme/.
The mini player and tray images come from the real desktop app; see
docs/RELEASING.md.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

from mutagen.id3 import APIC, ID3, TALB, TDRC, TIT2, TPE1, TRCK
from mutagen.mp3 import MP3

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "docs" / "readme"
TOKEN = "readme-token"


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def fetch(url: str, dest: Path) -> Path:
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "muse-readme-shots"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=120) as r, \
                        open(dest.with_suffix(".part"), "wb") as f:
                    shutil.copyfileobj(r, f)
                break
            except OSError:
                if attempt == 2:
                    raise
        dest.with_suffix(".part").rename(dest)
    return dest


def build_music(cache: Path, music: Path) -> None:
    library = json.loads((README / "demo-library.json").read_text())
    for album in library:
        a = slug(album["artist"]) + "--" + slug(album["album"])
        cover = fetch(album["cover_url"], cache / a / "cover.img").read_bytes()
        mime = "image/png" if cover[:4] == b"\x89PNG" else "image/jpeg"
        for track in album["tracks"]:
            src = fetch(track["url"], cache / a / f"{track['n']:02d}.mp3")
            d = music / album["artist"] / album["album"]
            d.mkdir(parents=True, exist_ok=True)
            dest = d / f"{track['n']:02d} - {slug(track['title'])}.mp3"
            shutil.copy(src, dest)
            ID3().save(dest)  # drop the original tags, keep the audio
            f = MP3(dest, ID3=ID3)
            f.tags.clear()
            f.tags.add(TIT2(encoding=3, text=track["title"]))
            f.tags.add(TPE1(encoding=3, text=album["artist"]))
            f.tags.add(TALB(encoding=3, text=album["album"]))
            f.tags.add(TRCK(encoding=3, text=str(track["n"])))
            if album.get("year"):
                f.tags.add(TDRC(encoding=3, text=str(album["year"])))
            f.tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=cover))
            f.save()
            lrc = README / "lyrics" / f"{slug(album['artist'])}-{slug(track['title'])}.lrc"
            if lrc.is_file():
                shutil.copy(lrc, dest.with_suffix(".lrc"))


def make_data(data: Path, cache: Path) -> None:
    music = data.parent / (data.name + "-music")
    shutil.rmtree(music, ignore_errors=True)
    build_music(cache, music)
    data.mkdir(parents=True, exist_ok=True)
    (data / "settings.json").write_text(json.dumps(
        {"music_folders": [str(music)], "music_files": [], "downloader_unlocked": False}))


def env_for(data: Path) -> dict:
    return {**os.environ,
            "MUSE_DATA_DIR": str(data), "MUSE_TOKEN": TOKEN,
            "MUSE_ART_BACKFILL": "0", "MUSE_LYRICS_SYNC": "0", "MUSE_TASTE_RETRAIN": "0",
            "MUSE_UPDATE_CHECK": "0", "MUSE_WEB_DIST": str(ROOT / "web" / "dist")}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--cache", type=Path, default=Path.home() / ".cache" / "muse-readme")
    p.add_argument("--data", type=Path, help="only build a data dir here and exit")
    args = p.parse_args()

    if args.data:
        make_data(args.data.resolve(), args.cache)
        print(f"MUSE_DATA_DIR={args.data.resolve()}")
        return

    tmp = Path(tempfile.mkdtemp(prefix="muse-readme-"))
    try:
        make_data(tmp / "data", args.cache)
        server = subprocess.Popen([sys.executable, "-m", "muse"], cwd=ROOT,
                                  env={k: v for k, v in env_for(tmp / "data").items()
                                       if k != "MUSE_PORT"},
                                  stdout=subprocess.PIPE, text=True)
        try:
            for line in server.stdout:
                if line.startswith("MUSE_LISTENING"):
                    port = int(line.split()[1])
                    break
            else:
                sys.exit("muse exited before listening")
            subprocess.run(["node", "scripts/readme-shots.mjs"], cwd=ROOT / "web", check=True,
                           env={**os.environ, "SHOTS_URL": f"http://127.0.0.1:{port}",
                                "SHOTS_TOKEN": TOKEN, "SHOTS_OUT": str(README)})
        finally:
            server.terminate()
            server.wait(timeout=10)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
