"""Freeze the ML worker (onedir, never onefile) into build/worker-dist/muse-worker/ and zip it as build/muse-worker-<target triple>.zip."""
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    env = {**os.environ, "PATH": f"{Path.home() / '.cargo' / 'bin'}{os.pathsep}{os.environ['PATH']}"}
    out = subprocess.run(["rustc", "-vV"], capture_output=True, text=True, check=True, env=env).stdout
    triple = next(l.split(":", 1)[1].strip() for l in out.splitlines() if l.startswith("host:"))
    work = ROOT / "build" / "pyinstaller-worker"
    PyInstaller.__main__.run([
        str(ROOT / "scripts" / "worker_entry.py"),
        "--onedir",
        "--noconfirm",
        "--log-level", "WARN",
        "--name", "muse-worker",
        "--distpath", str(ROOT / "build" / "worker-dist"),
        "--workpath", str(work),
        "--specpath", str(work),
        "--collect-submodules", "muse",  # Task 3: no other flags were needed (until the aligners)
        "--collect-data", "uroman",  # M4b preflight: romanization tables
        "--collect-data", "py3langid",  # M4b preflight: the language model ships in the package
    ])
    archive = Path(shutil.make_archive(
        str(ROOT / "build" / f"muse-worker-{triple}"), "zip",
        root_dir=ROOT / "build" / "worker-dist", base_dir="muse-worker",
    ))
    digest = hashlib.sha256()
    with open(archive, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    print(f"{archive} {archive.stat().st_size} {digest.hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
