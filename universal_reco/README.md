# universal_reco

A standalone, audio-only music embedding model — no metadata mirror, no
lyrics pipeline, no per-user history, no network calls at inference time.
Fully decoupled from the `muse` package; own dependencies, own lockfile.

## Status: content leg only

This currently ships exactly one function:

```python
from content_embed import embed

vector = embed("/path/to/track.mp3")  # np.ndarray, shape (768,), float32
```

Raw audio in, pretrained MERT-v1-95M content-embedding vector out. No
training or fine-tuning — the backbone is used as-is, since "universal"
means broad and general-purpose, and fine-tuning on any one library's
listening history would bias the embedding away from that.

It also ships a batch CLI, for a caller embedding a whole library in one
process load:

```sh
echo '{"id": 1, "path": "/music/track.mp3"}' | python -m content_embed.batch
# {"id": 1, "vec": [0.0123, ...]}
```

One JSON object per line in, one per line out, in order: `vec` on success,
`error` on failure. `id` is an opaque correlation token — this project never
interprets it. A track that fails to decode produces an error line and the run
continues.

A malformed request line does too, and reports as much of the id as it can.
Every input line produces exactly one output line and nothing can end the
process early, no matter which of these shapes shows up:

- not valid JSON at all (`not json`) — no id to recover, reports `null`.
- valid JSON but not an object (`[1, 2, 3]`, `42`, `"foo"`, `null`) — same,
  reports `null`.
- an object missing `"id"` — a vector with no id would be unattributable to
  any track on the caller's side, so this is an error, not a success; reports
  `null`.
- an object missing `"path"` — the id was right there, so it reports that
  `id` rather than `null`.

stdout is reserved for this protocol; nothing else is allowed to write there.
MERT loads via `trust_remote_code=True`, which runs code from the HF Hub, and
that code is known to `print()` a stray warning of its own to stdout the
first time a track is embedded. The CLI seizes the real stdout file
descriptor for protocol lines before the model is ever touched and points
fd 1 itself at stderr, so any such noise — from MERT, from a future
dependency, from anywhere — ends up on stderr instead of corrupting a
response line. An operator tailing this process's logs will see that noise
there, by design, not lost.

## Planned, not yet built

- **Mood leg** — audio-only valence-arousal embedding.
- **Lyrics-via-ASR leg** — on-device speech-to-text (Whisper) followed by a
  text embedder, chosen over predicting a lyrics-embedding directly from
  audio because that only ever learns genre/mood correlation, never actual
  lyrical content.
- Fusion of the legs into one similarity score, and any output/storage
  format for shipping vectors alongside synced audio files.
