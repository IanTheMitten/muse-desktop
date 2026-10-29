"""Freeze `python -m muse` into the Tauri sidecar (Decision 2): src-tauri/binaries/muse-core-<target triple>, the name bundle.externalBin expects. web/dist must be built first; the SPA ships inside."""
import os
import subprocess
import sys
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    dist = ROOT / "web" / "dist"
    if not (dist / "index.html").is_file():
        print("web/dist is missing: run `npm --prefix web run build` first")
        return 1
    env = {**os.environ, "PATH": f"{Path.home() / '.cargo' / 'bin'}{os.pathsep}{os.environ['PATH']}"}
    out = subprocess.run(["rustc", "-vV"], capture_output=True, text=True, check=True, env=env).stdout
    triple = next(l.split(":", 1)[1].strip() for l in out.splitlines() if l.startswith("host:"))
    PyInstaller.__main__.run([
        str(ROOT / "scripts" / "sidecar_entry.py"),
        "--onefile",
        "--noconfirm",
        "--log-level", "WARN",
        "--name", f"muse-core-{triple}",
        "--distpath", str(ROOT / "src-tauri" / "binaries"),
        "--workpath", str(ROOT / "build" / "pyinstaller"),
        "--specpath", str(ROOT / "build" / "pyinstaller"),
        "--add-data", f"{dist}{os.pathsep}web/dist",
        "--collect-submodules", "uvicorn",
        "--collect-submodules", "muse",
    ])
    return 0


if __name__ == "__main__":
    sys.exit(main())
