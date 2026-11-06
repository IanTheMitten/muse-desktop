# Releasing

Owner steps. Everything here needs write access to the GitHub repo and the
Hugging Face model repo.

## The app

The `package` GitHub Actions workflow (`.github/workflows/package.yml`) builds
macOS and Windows installers. It runs on manual dispatch and on tags named
`v*` or `ci-package-*`. The builds are unsigned. Release notes live in
`docs/release-notes/`.

## The ML worker

Freeze the worker on the machine you will run it on (needs Rust, for the target
triple):

```sh
uv run --extra embed --group package python scripts/freeze_worker.py
```

It writes `build/muse-worker-<target triple>.zip` and prints its size and
sha256. The `package` workflow's `worker` job does the same on macOS and
Windows.

After the CI run produces the zips:

1. Upload the five Laya files from `~/models/laya-muse-v4` (`encoder/config.json`,
   `model.safetensors`, `rl_agent_config.json`, `tokenizer/tokenizer.json`,
   `tokenizer/tokenizer_config.json`) to Hugging Face model repo
   `IanTheMitten/laya-muse-v4`. A different repo name means re-running
   `uv run python scripts/build_manifest.py --laya-repo <name>`.
2. Make the GitHub repo public. Create release `worker-v1` from the CI
   artifacts `muse-worker-aarch64-apple-darwin.zip` and
   `muse-worker-x86_64-pc-windows-msvc.zip`.
3. For each zip run
   `uv run python scripts/manifest_add_worker.py <triple> <zip> <url>`, commit
   `muse/smart_manifest.json`, and rebuild the app.

## README images

The screenshots and the demo GIF in `docs/readme/` come from a script, run
against a demo library of freely licensed music:

```sh
npm --prefix web run build
npx --prefix web playwright install chromium   # once
uv run python scripts/readme_shots.py          # SHOTS_ONLY=hero,mini,demo to redo some
```

It needs `ffmpeg` on the path for the GIF. The tray menu is native, so it
has no image.

See [docs/readme/CREDITS.md](readme/CREDITS.md) for the music and its licences.
