# M3 CI packaging matrix results

- Run: 40878798529 — https://github.com/IanTheMitten/muse-desktop/actions/runs/40878798529
- Trigger: tag `ci-package-m3-1` (branch `m3-ci`)
- **Conclusion: failure** (macos-15 success, windows-latest failure)
- Duration: 2026-10-29 13:05:37Z to 13:21:01Z, about 15m24s

## Steps

| Step | macos-15 | windows-latest |
|---|---|---|
| checkout / setup-uv / setup-node / rust-toolchain | success | success |
| npm ci | success | success |
| npm run build (web) | success | success |
| freeze_sidecar.py | success | success |
| tauri build | success | success |
| upload bundle artifact | success | success |
| shell_smoke.py | success | **failure** |
| upload smoke report | success | success |

## Smoke

macos-15 (`SHELL-SMOKE-OK`): health=200, media_session=True, muse-core not left running (no "sidecar left running" output).

| format | can_play_type | result |
|---|---|---|
| mp3 | maybe | decoded |
| flac | maybe | decoded |
| m4a | maybe | decoded |
| wav | maybe | decoded |
| ogg | probably | decoded |
| opus | probably | decoded |

windows-latest (`SHELL-SMOKE-FAIL`): health=200, media_session=True, formats `{}` (none probed; "not decoded: mp3, flac, m4a, wav"). muse-core not left running (no "sidecar left running" output). Report: `{"formats":{},"health":200,"media_session":true}`.

## Bundles

- macOS: `dmg/Muse_0.1.0_aarch64.dmg` — 44,097,796 bytes
- Windows: `nsis/Muse_0.1.0_x64-setup.exe` — 64,638,695 bytes

## Windows failure (last 30 lines of `--log-failed`, step prefix stripped)

```
shell: C:\Program Files\Git\bin\bash.EXE --noprofile --norc -e -o pipefail {0}
env:
  UV_TOOL_BIN_DIR: D:\a\_temp\uv-tool-bin-dir
  UV_TOOL_DIR: D:\a\_temp\uv-tool-dir
  UV_PYTHON_INSTALL_DIR: D:\a\_temp\uv-python-dir
  CARGO_HOME: C:\Users\runneradmin\.cargo
  CARGO_INCREMENTAL: 0
  CARGO_TERM_COLOR: always
health=200 media_session=True
not decoded: mp3, flac, m4a, wav
Traceback (most recent call last):
  File "sidecar_entry.py", line 2, in <module>
  File "pyimod02_importers.py", line 457, in exec_module
  File "muse\__main__.py", line 20, in <module>
    from .api import create_app
  File "pyimod02_importers.py", line 457, in exec_module
  File "muse\api.py", line 45, in <module>
    from .jobs import (
  File "pyimod02_importers.py", line 457, in exec_module
  File "muse\jobs.py", line 20, in <module>
    from . import content_embed_batch
  File "pyimod02_importers.py", line 457, in exec_module
  File "muse\content_embed_batch.py", line 17, in <module>
    import fcntl
ModuleNotFoundError: No module named 'fcntl'
[PYI-668:ERROR] Failed to execute script 'sidecar_entry' due to unhandled exception!
SHELL-SMOKE-FAIL
##[error]Process completed with exit code 1.
```

## Follow-ups (not applied)

- Windows: `muse/content_embed_batch.py:17` imports POSIX-only `fcntl` at module top, so the frozen sidecar crashes on import. Fix: guard it (import lazily or under `sys.platform != "win32"`, with an `msvcrt` or no-op fallback). That module belongs to the content leg retired in M4.
- Windows: the smoke reported health=200 and media_session=True although the sidecar crashed. The health probe probably hit the shell's webview or something other than the sidecar; check that `shell_smoke.py` asserts health against the sidecar before trusting that number.
- Re-run the Windows leg after the fcntl fix to get real codec results (WebView2 decode of mp3/flac/m4a/wav is still unconfirmed).
