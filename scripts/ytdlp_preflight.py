"""Preflight: official yt-dlp binary + m4a path (itag 140) with no JS runtime."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import requests

BASE = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/"
ASSETS = {"linux": "yt-dlp_linux", "darwin": "yt-dlp_macos", "win32": "yt-dlp.exe"}


def run(cmd, timeout):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="build/ytdlp-preflight")
    ap.add_argument("--query", default="ytsearch1:Rick Astley - Never Gonna Give You Up")
    args = ap.parse_args()
    out = Path(args.out)
    report: dict = {}
    failed = ""

    def step(letter, fn):
        nonlocal failed
        if failed:
            return
        try:
            fn()
        except Exception as error:  # record, stop at first failure
            report[f"{letter}_error"] = f"{type(error).__name__}: {error}"
            failed = letter

    state: dict = {}

    def a():
        asset = ASSETS[sys.platform]
        sums = requests.get(BASE + "SHA2-256SUMS", timeout=300)
        sums.raise_for_status()
        want = next(l.split()[0] for l in sums.text.splitlines() if l.split()[-1].lstrip("*") == asset)
        body = requests.get(BASE + asset, timeout=300)
        body.raise_for_status()
        got = hashlib.sha256(body.content).hexdigest()
        report["sha256_ok"] = got == want
        if got != want:
            raise RuntimeError("sha256 mismatch")
        binp = out / "bin" / asset
        binp.parent.mkdir(parents=True, exist_ok=True)
        binp.write_bytes(body.content)
        binp.chmod(0o755)
        state["bin"] = str(binp)

    def b():
        report["version"] = run([state["bin"], "--version"], 120).stdout.strip()

    def c():
        r = run([state["bin"], "--ignore-config", "--no-playlist", "-J", args.query], 900)
        report["probe_rc"] = r.returncode
        report["probe_warnings"] = [l for l in r.stderr.splitlines() if "WARNING" in l]
        if r.returncode:
            report["probe_stderr_tail"] = r.stderr.splitlines()[-3:]
            raise RuntimeError("probe failed")
        info = json.loads(r.stdout)
        e = (info.get("entries") or [info])[0]
        report["entry"] = {k: (k in e and e[k] is not None) for k in ("id", "title", "duration", "thumbnail")}
        report["entry_id"] = e.get("id")
        report["audio_formats"] = [
            [f.get("format_id"), f.get("ext"), f.get("acodec")]
            for f in e.get("formats", []) if f.get("vcodec") == "none"
        ]
        state["duration"] = e.get("duration")

    def d():
        dl = out / "dl"
        r = run([state["bin"], "--ignore-config", "--no-warnings", "--no-playlist", "--no-progress",
                 "--force-overwrites", "-f", "bestaudio[ext=m4a]", "-o", str(dl / "track.%(ext)s"),
                 "-J", "--no-simulate", args.query], 900)
        report["download_rc"] = r.returncode
        report["download_stderr_last"] = (r.stderr.splitlines() or [""])[-1]
        f = dl / "track.m4a"
        report["file_size"] = f.stat().st_size if f.exists() else None
        if r.returncode or not f.exists():
            raise RuntimeError("download failed")
        state["file"] = f

    def e():
        from mutagen.mp4 import MP4

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from muse.ingest import tag_m4a
        from muse.remux import remux_m4a

        remux_m4a(state["file"])
        report["probe_duration"] = state["duration"]
        report["mp4_length"] = MP4(state["file"]).info.length
        report["remux_delta_s"] = report["mp4_length"] - (state["duration"] or 0)
        tag_m4a(state["file"], artist_name="A", album_name="B", track_title="T",
                track_id="x", track_no=1, total_tracks=1, disc_no=None)
        report["title_readback"] = MP4(state["file"])["\xa9nam"]
        if report["title_readback"] != ["T"]:
            raise RuntimeError("tag readback mismatch")

    for letter, fn in zip("abcde", (a, b, c, d, e)):
        step(letter, fn)
    print(json.dumps(report, indent=2))
    print(f"PREFLIGHT-FAIL {failed}" if failed else "PREFLIGHT-OK")


if __name__ == "__main__":
    main()
