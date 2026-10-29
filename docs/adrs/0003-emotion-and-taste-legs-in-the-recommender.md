# ADR 0003: The recommender gains an emotion leg and replaces feedback_score with a learned taste embedding

- **Status:** Accepted
- **Date:** 2026-08-11
- **Slice:** 2 (Recommender)
- **Related:** `docs/prds/2026-08-06-muse-player.md` (Recommender section), `docs/superpowers/specs/2026-06-20-music-recommendation-design.md`

## Context

The PRD's slice 2 recommender fuses three content legs (CLAP audio, Spotify
features, MiniLM lyrics) with a hand-tuned `feedback_score` scalar:

```
score = w_clap · clap_sim + w_feat · feature_sim + w_lyr · lyrics_sim + w_fb · feedback_score
```

`feature_vec` carries Spotify's `valence`, a single positive↔negative axis
that can't distinguish "angry" from "sad" (both low valence) or "calm" from
"happy" (both high valence). Neither `feature_vec` nor `lyrics_vec` was
designed to capture emotion precisely — the goal from the outset was more
precise mood matching, with an explainable "reason" as a side effect.

Separately, `feedback_score` is a per-track time-decayed scalar over discrete
events. It cannot capture *co-occurrence* — that track A often gets played
after track B regardless of content similarity — which is closer to what the
PRD's future learned MLP scorer was reserved for, but that scorer explicitly
waits for training data that doesn't exist yet.

`feature_vec` is also null for any track the Spotify catalog mirror never
saw (post-freeze releases, non-catalog ingest) — the same gap CLAP was
introduced to solve for audio similarity in the first place.

## Decision

**Emotion leg.** A new `emotion_vec` (2D valence-arousal point) is computed
per track by fusing two sources:

- **Audio-VA** — a pretrained music-emotion-recognition model (trained on a
  real MER dataset, e.g. DEAM/PMEmo/Emomusic), run against the audio file
  directly. Always available, same reasoning as CLAP: it doesn't depend on
  the Spotify catalog mirror, so post-freeze and non-catalog tracks are
  covered from day one.
- **Lyrics-VA** — a text-based emotion model over the cleaned lyrics. Null
  for instrumentals.

`emotion_vec = w · audio_va + (1 − w) · lyrics_va`, falling back to
`audio_va` alone when `lyrics_va` is null. `emotion_sim` is **Euclidean
distance converted to similarity**, not cosine — VA is a bounded plane where
proximity of two points is the meaningful relationship (the circumplex
model), unlike the high-dimensional L2-normalized vectors the other legs
compare by angle.

**Taste leg replaces `feedback_score`.** A `taste_embeddings` table holds a
per-track vector learned by implicit-ALS matrix factorization over the
existing `feedback_score` values (the same time-decayed skip/full_listen/
replay/thumbs weights already defined for the old scalar — one event-
weighting scheme, not two). This is a separate table from `embeddings`
because its lifecycle is different: content embeddings are computed once per
track and only recompute when their source changes; the taste embedding is a
whole-library snapshot that gets replaced wholesale on a **nightly retrain
job**.

`taste_sim` is **null until a minimum play-event threshold is crossed**,
below which its weight renormalizes across the remaining legs — the same
null-leg pattern `feature_vec` already uses for instrumentals and post-freeze
tracks.

`rank()`'s signature is unchanged; it now fuses five legs instead of four:

```
score = w_clap · clap_sim + w_feat · feature_sim + w_lyr · lyrics_sim
      + w_emo  · emotion_sim + w_taste · taste_sim
```

## Consequences

**Good.** Emotion matching gets a second axis (arousal) that valence alone
never had, with audio-VA covering exactly the tracks `feature_vec` can't.
Taste embedding captures co-occurrence patterns `feedback_score` structurally
cannot, while still degrading gracefully to the fixed-weight legs before
enough listening history exists — nothing regresses on day one.

**Bad.** Two new dependencies (a pretrained MER model, an ALS library) join
CLAP and MiniLM. The nightly retrain job is a new failure mode to monitor —
a bad retrain silently replaces a working taste embedding with a worse one,
with no rollback mechanism specified yet.

**Bad.** `feedback_score`'s hand-tuned weights (thumbs_down = −3.0, etc.) are
now load-bearing in two places — the taste embedding's training input, and
nowhere else, since the scalar itself retires as a `rank()` leg. Retuning
those weights later means retraining, not just re-fusing.

**Binding.** No leg may assume `taste_sim` or `emotion_vec` (lyrics side) is
present. Both are optional inputs to `rank()`'s weight renormalization, same
contract as `feature_vec` today.
