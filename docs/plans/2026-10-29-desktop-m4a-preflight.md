# M4a Task 3 preflight results

Free disk at start: 24G (step 1 passed).

## Versions resolved (`uv tree --depth 1 --package muse`)

torch 2.15.0+cpu (pytorch-cpu index on Linux), transformers 5.22.0, sentence-transformers 6.2.0, av 19.0.2, laya 0.3.20, numpy 2.5.2, huggingface-hub 2.4.0 (lock moved 1.27.0 -> 2.4.0), tokenizers 0.23.3. CUDA/nvidia/triton packages dropped from the lock. PyInstaller 6.22.3 (contrib hooks 2026.9).

## Model table (CPU, load from local HF snapshot dirs; `scripts/worker_preflight.py`)

| model | load s | one inference s | output shape |
|---|---|---|---|
| mert (AutoModel trust_remote_code, 5 s of tone) | 2.9 | 0.16 | [768] |
| clap (ClapModel/ClapProcessor, 1 s @ 48 kHz) | 0.6 | 0.91 | [512] |
| minilm (SentenceTransformer) | 2.7 | 0.08 | (384,) |
| audio_va (`_build_audio_va_model`, 1 s @ 16 kHz) | 0.2 | 1.35 | [3] (arousal, dominance, valence) |
| lyrics_va (pipeline text-classification, top_k=None) | 0.4 | 0.30 | (7,) |
| laya (`laya.load(~/models/laya-muse-v4, cpu)`, one `system_one` choice question) | 9.8 | 0.12 | 3 keys in reply |

All six loaded and answered. Weights came from the HF cache snapshot dirs (`snapshot_download(local_files_only=True)`), no network.

## Decode (PyAV, mono float32; fixtures are 1.000 s tones)

| format | 16000 Hz | 24000 Hz | 48000 Hz |
|---|---|---|---|
| flac | 16000 (1.000 s) | 24000 | 48000 |
| m4a | 16000 (1.000 s) | 24000 | 48000 |
| mp3 | 16000 (1.000 s) | 24000 | 48000 |
| ogg | 15954 (0.997 s) | 23931 | 47861 |
| opus | 16000 (1.000 s) | 24000 | 48000 |
| wav | 16000 (1.000 s) | 24000 | 48000 |

ogg is 0.3% short (Vorbis priming/trailing frames); the plan's +-1% window tolerance and 0.1 s duration tolerance hold.

## Freeze

Flags (final, verbatim; attempt 1 of 5, no extra collect/hidden-import flags were needed):

```
uv run --extra embed --group package pyinstaller scripts/preflight_freeze_entry.py --onedir --noconfirm --distpath build/preflight/dist --workpath build/preflight/work --specpath build/preflight
```

Result: exe printed `FREEZE-OK` (torch, transformers, sentence_transformers, av, laya imported; MERT loaded with `trust_remote_code=True` from the local snapshot and one forward on 1 s zeros at 24 kHz; `laya.load` + one `system_one`).
`du -sh build/preflight/dist`: 1.2G. Build time about 168 s. Run time: a few seconds to FREEZE-OK (not separately timed).

## Errors met and fixes

1. **MERT `hidden_states` is None on transformers 5.22.** `m(**x, output_hidden_states=True)` returns `BaseModelOutput` with `hidden_states=None` (also with `return_dict=True`). Cause: the MERT remote `forward` bypasses transformers 5's `capture_outputs` recording. Task 4's `MertEncoder` must not read `outputs.hidden_states`; collect the 13 states with hooks: a forward pre-hook on `m.encoder.layers[0]` (its input = state 0) plus a forward hook on each of the 12 `m.encoder.layers[i]` (output, `o[0]` if tuple). The preflight script does this and gets 13 states, mean over time then over layers -> [768]. (Equivalence to the old 4.x `hidden_states` tuple assumed from HubertEncoder semantics, not compared numerically against stored vectors; Task 4 should compare against an existing stored MERT row if one is on hand.)
2. Noise only, no fix: `WARNING: feature_extractor_cqt requires the libray 'nnAudio'` (MERT-95M config has the CQT branch off in practice; output fine); `use_return_dict is deprecated`; PyInstaller warnings about missing CUDA/HIP ctypes libs and `torch.utils.tensorboard`.
3. `uv tree --extra embed` is not a valid flag; used `uv tree --depth 1 --package muse`.
4. Frozen exe pulled the build venv's full site-packages closure (scipy, sklearn, pytest hooks ran): size 1.2G could shrink with `--exclude-module` flags; not needed for the verdict.

## Verdict

Verdict: positive. `embed` extra resolves with CPU torch on Linux, all six models load from local dirs and answer on this machine, PyAV decodes all six formats at 16/24/48 kHz, and a PyInstaller onedir build runs with no extra flags (flags list above). One required code change for Task 4: MERT hidden states via hooks (error 1). macOS/Windows freezes are untested here (Linux only).
