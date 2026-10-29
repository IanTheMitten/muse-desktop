# M4b Task 1 preflight notes

Run: `uv run --extra embed python scripts/align_preflight.py` (Linux x86_64, CPU torch 2.15.0, transformers 5.22.0). Last line `PREFLIGHT-OK`.

| Check | Result | Seconds | Peak RSS |
|---|---|---|---|
| schufo weights download (3 files, hash-checked) | ok | 7.1 | 246 MB |
| schufo `load_state_dict(strict=True)` | ok | 0.3 | 325 MB |
| schufo on tone.wav, 12 tokens | scores `(1, 61, 12)` | 0.1 | 364 MB |
| schufo 240 s noise, 1500 tokens | scores `(1, 14999, 1500)` | 47.5 | 1113 MB |
| MMS-FA snapshot download (3 files) | ok | 75.8 | 1398 MB |
| MMS-FA `Wav2Vec2ForCTC.from_pretrained(dir)` | ok | 7.7 | 1398 MB |
| MMS-FA on tone.wav | logits `(1, 49, 31)` | 1.1 | 1995 MB |
| MMS-FA 8 x 34 s windows, batches of 4 | ok | 86.3 | 4983 MB |
| `uroman.Uroman()` init | ok | 3.7 | 4983 MB |
| uroman: `사랑해 너를`/kor, `愛してる`/jpn, `hello`/None | `saranghae neoreul`, `aishiteru`, `hello` | 0.0 | 4983 MB |
| py3langid.classify | `en`, `ko`, `ja` | 0.4 | 4983 MB |

Notes:
- stft fix: `STFT.forward` replaced by `torch.view_as_real(torch.stft(..., return_complex=True))`, then the same `.contiguous().view(n, c, n_fft//2+1, -1, 2)`. Model input is `forward([mix (1,1,T) float32, tokens (1,N) long])`; output `(x, alphas, scores)`; scores are `(1, frames, tokens)`.
- `weights_only`: `torch.load(..., weights_only=True)` succeeded; no fallback needed.
- MMS-FA memory: 4983 MB peak RSS for batch 4 x 34 s on CPU (one 34 s window is ~1 GB over the loaded model); consider batch size/window when sizing the worker.
- schufo 240 s: 47.5 s on CPU, ~1.1 GB (DTW matrix over 15k frames x 1500 tokens dominates).
- PyInstaller hints: `--collect-data uroman`, `--collect-data py3langid` expected (py3langid ships its model inside the package, 4.4 MiB; uroman ships data files). Not yet frozen-tested.
- Deps added to `embed` extra: `uroman==1.3.1.1`, `py3langid==0.4.0`.

verdict: go
