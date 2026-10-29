#!/usr/bin/env bash
# The Tauri CLI with one shared Cargo target dir, so each git worktree does not build its own ~3 GB copy.
set -euo pipefail
export PATH="$HOME/.cargo/bin:$PATH"
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$HOME/.cache/muse-tauri-target}"
cd "$(dirname "$0")/.."
exec web/node_modules/.bin/tauri "$@"
