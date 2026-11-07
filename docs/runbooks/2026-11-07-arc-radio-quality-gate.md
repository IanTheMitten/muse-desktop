# Arc radio quality gate runbook

For the owner, on the server. Spec: `docs/specs/2026-11-07-arc-radio-and-model-retirement.md`, "Quality gate". The branch must not merge until both gates pass and their outputs are posted on the PR.

Migration 23 drops columns and cannot be undone. Never run a gate against the live data dir with the branch code. Every branch-side step below uses a copy of the data dir or of `library.sqlite3`.

## Steps

1. **Gate 1, before upgrading: CLAP energy vs. stored arousal.**
   1. Copy the live `library.sqlite3` (still schema version 22) aside, for example to `/tmp/library-v22.sqlite3`.
   2. From a checkout of the branch, run:
      ```sh
      uv run python scripts/energy_check.py --db /tmp/library-v22.sqlite3
      ```
      The script opens the copy read-only and starts the ML worker. It uses `MUSE_WORKER_CMD` when set, else the installed worker under `<models dir>/worker/muse-worker/`. `--worker-cmd "<command>"` overrides both. `--db` defaults to the library in the current data dir.
   3. It prints `n=`, `rho=` and `PASS` or `FAIL (gate: rho >= 0.5)`. Exit code 0 is pass. Pass is `rho >= 0.5`.
   4. On fail, revise `CALM_PROMPT` and `ENERGETIC_PROMPT` in `muse/energy.py` and rerun.

2. **Gate 2: `arc_eval`, main against branch.**
   1. On `main`, against the live data dir (the harness is read-only), save the output:
      ```sh
      uv run python -m muse.arc_eval | tee arc_eval-main.txt
      ```
   2. Copy the whole data dir (for example to `/tmp/muse-data-copy`).
   3. On the branch, start the app once on the copy so migration 23 runs and the launch chain fills `prompt_vectors`:
      ```sh
      MUSE_DATA_DIR=/tmp/muse-data-copy uv run python -m muse
      ```
      Wait until the launch chain finishes (watch the log or `/api/health`), then stop it.
   4. Still on the branch, run the harness on the copy and save the output:
      ```sh
      MUSE_DATA_DIR=/tmp/muse-data-copy uv run python -m muse.arc_eval | tee arc_eval-branch.txt
      ```
   5. Pass is theme hold and curve error no worse than the `main` run.

   **Smart-features caveat for step 2.3.** The prompt fill and the `energy_ready` report run in the launch chain only when smart features count as installed or `MUSE_EMBED_BATCH` is set (`muse/api.py`: `config.embed_batch or smart.installed(config.models_dir)`). `smart.installed` is true only when `installed.json` in the models dir has a `manifest_version` equal to the branch's `muse/smart_manifest.json` version. After the upgrade the app may therefore report smart features as not installed. If so, click Install in Settings once (it re-installs and bumps `installed.json`), or start with `MUSE_EMBED_BATCH=1` and `MUSE_WORKER_CMD` pointing at the worker. Without one of these, `prompt_vectors` stays empty and the branch `arc_eval` numbers are not valid.

3. **Report only: `reco_eval`.** Run on both sides, same data as in step 2 (main on the live data dir, branch with `MUSE_DATA_DIR=/tmp/muse-data-copy`). It takes about 4 minutes and is not gating.
   ```sh
   uv run python -m muse.reco_eval
   ```

4. **Post and decide.** Post the Gate 1 output, both `arc_eval` outputs and both `reco_eval` outputs on the PR. Do not merge until both gates pass.
