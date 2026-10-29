"""Drive a built Tauri shell against the committed fixtures: the packaging smoke
of docs/specs/2026-10-29-desktop-app-fork.md (lines 645-654).

The shell, started with MUSE_SMOKE_REPORT set, writes a JSON report:
{"health": <status>, "media_session": ..., "formats": {"mp3": {"can_play_type": ..., "result": ...}}}
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "smoke"


def sidecar_running() -> bool:
    if sys.platform == "win32":
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq muse-core.exe"], capture_output=True, text=True).stdout
        return "muse-core.exe" in out
    return subprocess.run(["pgrep", "-x", "muse-core"], capture_output=True).returncode == 0


def start_xvfb():
    r, w = os.pipe()
    proc = subprocess.Popen(
        ["Xvfb", "-displayfd", str(w), "-screen", "0", "1280x800x24", "-nolisten", "tcp"],
        pass_fds=(w,),
    )
    os.close(w)
    with os.fdopen(r) as f:
        n = f.readline().strip()
    return proc, f":{n}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--build", action="store_true", help="build web, freeze the sidecar, build the debug shell first")
    ap.add_argument("--binary", help="path to the shell binary")
    ap.add_argument("--expect", required=True, help="comma list of formats that must decode")
    ap.add_argument("--report-out", help="copy the shell's report here")
    args = ap.parse_args()

    if args.build:
        for cmd in (
            ["npm", "--prefix", "web", "run", "build"],
            ["uv", "run", "--group", "package", "python", "scripts/freeze_sidecar.py"],
            ["bash", "scripts/tauri.sh", "build", "--debug", "--no-bundle"],
        ):
            if subprocess.run(cmd, cwd=ROOT).returncode:
                print("SHELL-SMOKE-FAIL")
                return 1

    binary = args.binary or str(
        Path(os.environ.get("CARGO_TARGET_DIR") or Path.home() / ".cache" / "muse-tauri-target") / "debug" / "muse-shell"
    )
    if sys.platform == "win32" and not os.path.exists(binary):
        binary += ".exe"

    tmp = Path(tempfile.mkdtemp(prefix="muse-shell-smoke-"))
    data = tmp / "data"
    data.mkdir()
    (data / "settings.json").write_text(json.dumps({"music_folders": [str(FIXTURES)], "downloader_unlocked": False}))
    report = tmp / "report.json"
    env = {**os.environ, "MUSE_DATA_DIR": str(data), "MUSE_SMOKE_REPORT": str(report)}

    xvfb = None
    if sys.platform == "linux" and not env.get("DISPLAY"):
        xvfb, env["DISPLAY"] = start_xvfb()

    ok = False
    try:
        try:
            subprocess.run([binary], env=env, timeout=180)
        except subprocess.TimeoutExpired:
            print("shell did not exit within 180 s")
        if not report.is_file():
            print("report missing")
        else:
            rep = json.loads(report.read_text())
            fmts = rep.get("formats", {})
            for name, row in fmts.items():
                print(f"{name:6} can_play_type={row.get('can_play_type')!r} result={row.get('result')}")
            print(f"health={rep.get('health')} media_session={rep.get('media_session')}")
            expect = [e.strip() for e in args.expect.split(",") if e.strip()]
            missing = [e for e in expect if fmts.get(e, {}).get("result") != "decoded"]
            if missing:
                print("not decoded:", ", ".join(missing))
            ok = rep.get("health") == 200 and not missing
            if args.report_out:
                shutil.copyfile(report, args.report_out)
        deadline = time.time() + 15
        while sidecar_running() and time.time() < deadline:
            time.sleep(0.5)
        if sidecar_running():
            print("sidecar left running")
            ok = False
    finally:
        if xvfb:
            xvfb.terminate()
    if not ok:
        log = data / "muse.log"
        if log.is_file():
            print("\n".join(log.read_text(errors="replace").splitlines()[-30:]))
    print("SHELL-SMOKE-OK" if ok else "SHELL-SMOKE-FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
