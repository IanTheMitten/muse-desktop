"""Write latest.json, the feed the shell's updater reads, for one release.

Run by the release-app job over the downloaded bundles (dist/). Each platform
must have exactly one update file and its .sig; a missing one fails the release
rather than publishing a feed that sends some installs nowhere.

    python scripts/updater_manifest.py --dist dist --tag v0.1.5 --repo owner/name
"""

from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
import sys

# Updater platform key -> the update file under dist/. macOS updates from the
# .app tarball; Windows runs the NSIS installer itself.
PLATFORMS = {
    "darwin-aarch64": "macos/*_aarch64.app.tar.gz",
    "darwin-x86_64": "macos/*_x64.app.tar.gz",
    "windows-x86_64": "nsis/*_x64-setup.exe",
}


def manifest(dist: str, tag: str, repo: str, now: datetime.datetime) -> dict:
    base = f"https://github.com/{repo}/releases/download/{tag}/"
    platforms = {}
    for key, pattern in PLATFORMS.items():
        matches = glob.glob(os.path.join(dist, pattern))
        if len(matches) != 1:
            raise SystemExit(f"expected exactly one {pattern} for {key}, found {len(matches)}")
        path = matches[0]
        try:
            with open(path + ".sig") as f:
                signature = f.read().strip()
        except FileNotFoundError:
            raise SystemExit(f"missing signature {path}.sig") from None
        platforms[key] = {"signature": signature, "url": base + os.path.basename(path)}
    return {
        "version": tag.removeprefix("v"),
        "notes": f"https://github.com/{repo}/releases/tag/{tag}",
        "pub_date": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "platforms": platforms,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dist", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--out", default=None, help="default: <dist>/latest.json")
    args = ap.parse_args(argv)
    data = manifest(args.dist, args.tag, args.repo, datetime.datetime.now(datetime.timezone.utc))
    out = args.out or os.path.join(args.dist, "latest.json")
    with open(out, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    print(f"wrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
