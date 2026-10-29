# ADR 0004: The recommender gains a MERT content leg, and it shares CLAP's weight budget

- **Status:** Accepted
- **Date:** 2026-08-24
- **Slice:** 2 (Recommender)
- **Related:** `docs/adrs/0003-emotion-and-taste-legs-in-the-recommender.md`,
  `docs/superpowers/specs/2026-08-24-content-embed-leg-design.md`,
  `universal_reco/README.md`

## Context

ADR 0003 fixed a five-leg fusion: CLAP audio, Spotify features, MiniLM
lyrics, emotion, and taste. `universal_reco` now supplies a sixth candidate —
a 768-dimension MERT-v1-95M content embedding, computed from the audio file
alone, with no catalog dependency and no lyrics dependency.

It is validated in isolation: a confound-controlled same-artist ranking test
over real library tracks, restricted to cross-album pairs so a shared
mastering fingerprint could not carry the result, beat a random baseline
2.83 vs. 6.0 mean rank.

Two problems stand between that and a working leg.

Raw MERT cosines cluster in a 0.91–0.98 band. Within the leg that is
harmless — ranking cares only about order — but fused against legs spanning
most of [0, 1], a 0.07-wide range contributes nothing at any weight. The
cause is anisotropy: the embeddings share a dominant mean component.

And `content_vec` and `audio_vec` are both content embeddings of the same
signal, the audio file. ADR 0003's formula reads as five legs each covering a
gap the others cannot; a sixth leg that overlaps an existing one at an
independent weight quietly doubles audio's influence against lyrics, emotion,
and taste.

`rank()` — the function that would actually fuse these into one score — does
not exist yet; `muse/reco.py`'s module docstring names it `rank() (ticket
05)`. This ADR fixes the contract that function must honor once it is
written; it does not describe code that runs today.

## Decision

**A sixth leg, normalized against corpus statistics.** `content_vec` is
stored **raw**, not L2-normalized like `audio_vec` and `lyrics_vec` —
centering has to happen before any rescale, so normalizing at write time
would destroy the information the correction operates on. `content_sim`
(`muse/reco.py`) mean-centers both vectors by the corpus mean before taking
their cosine, then maps the centered cosine into `[0, 1]` with a **clamped
affine map anchored on two measured percentiles** of this corpus's own
centered-cosine distribution (`sim_lo`/`sim_hi`, the 1st and 99th):

```
content_sim = clamp((centered_cosine - sim_lo) / (sim_hi - sim_lo), 0, 1)
```

This is not a z-score. A z-score would rescale by the distribution's mean
and standard deviation and leave an unbounded tail to clamp separately;
anchoring on measured percentiles instead lands the output in `[0, 1]`
directly, with the clamp doing double duty as the tail handling. Centering
addresses the cause of the compression; the percentile anchors are a
rescale of whatever spread remains once cosines are no longer computed
against a shared mean component. Both statistics live in `content_stats`
(`mean_vec`, `sim_lo`, `sim_hi`, `track_count`, `computed_at`), are
recomputed wholesale by every batch (`recompute_stats` in `muse/content.py`),
and are passed into `muse.reco` as arguments rather than read by it —
`reco.py`'s module docstring commits it to staying pure and testable without
a database.

**The leg is unavailable, not degenerate, when the corpus cannot calibrate
it.** `recompute_stats` refuses to write a `content_stats` row below
`MIN_STATS_TRACKS = 3` vectors, and refuses again if the computed anchors
coincide even above that floor. The reason for the floor is algebraic, not a
round number: with exactly two *distinct* vectors the corpus mean sits
exactly between them, so the centered pair are exact negatives of each other
and their cosine is −1.0 (a duplicate-vector pair centers to two zero
vectors instead, and `cosine_sim` calls that 0.0 rather than −1.0 — either
way it is one point, not a distribution). So `sim_lo` and `sim_hi`
both land on that single value. Since `content_sim` raises on `hi <= lo`,
that row would make every subsequent ranking call raise, in a perfectly
ordinary state: a
fresh library that has just embedded its first two tracks. Three mutually
equidistant vectors collapse the same way, which is why the floor alone is
necessary but not sufficient — `recompute_stats` also checks the *outcome*
and leaves `content_stats` untouched if `sim_hi <= sim_lo` after computing
it. Either guard failing leaves the table exactly as it was before the
batch ran, which reads as "no statistics yet" — the same absent-leg state
`rank()` will have to handle regardless, not a new failure mode it must grow
a case for.

**`content_sim` raises, rather than degrades, when a stored statistics row
cannot be trusted.** It checks `hi <= lo` (a degenerate range divides by
zero; an inverted range `hi < lo` doesn't, but silently scores identical
vectors at 0.0 and opposite ones at 1.0 — worse than a crash) and that
`mean` is the same length as `a` and `b`. These guard two different
failure classes, both traceable to `recompute_stats`, `content_stats`'s
only writer — but with different strength of guarantee. The anchor-range
check is one `recompute_stats` cannot violate: it re-checks its own
`sim_hi <= sim_lo` outcome and leaves the row untouched rather than write
one that would fail it (see above). The length check guards a *staleness*
seam it cannot close by itself: `recompute_stats` raises `ValueError` on a
mixed-width corpus rather than average across it, so once a model change
widens `CONTENT_DIM`, the stats row stops updating and keeps its old width
until every pre-change vector is gone — and a `content_sim` call in the
meantime, comparing that stale `mean` against a newly-embedded, wider
vector, correctly raises instead of truncating through `zip`. Absence is
the one case neither check is for — `stats() is None`, handled by leaving
the leg out of the fusion, not by raising.

**The two audio legs share one weight budget.**

```
w_clap + w_content = w_audio
```

`rank()` must treat them as one term with an internal split, not two
independent terms. The validation measured MERT alone; it says nothing about
MERT-versus-CLAP or MERT-plus-CLAP, so neither retiring CLAP nor promoting
MERT to a full independent leg is supported by what is known today. A later
A/B moves the split without changing the formula's shape.

ADR 0003's formula becomes:

```
score = w_audio · (split of clap_sim and content_sim)
      + w_feat · feature_sim + w_lyr · lyrics_sim
      + w_emo  · emotion_sim + w_taste · taste_sim
```

**The leg is computed out of process.** `universal_reco` keeps its own
virtualenv, lockfile, and Python floor; muse invokes its batch CLI over a
stdin/stdout line protocol and never imports it.

## Consequences

**Good.** A validated content signal enters the recommender with no new
dependency in muse's own environment, and with a normalization derived from
measurement rather than guessed. The leg degrades to absent — the same
null-leg contract `feature_vec` and `taste_vec` already have — including in
the corpus states (too few tracks, a degenerate distribution) that a naïve
implementation would instead have turned into a crash.

**Bad.** A third scheduled systemd timer (after `muse-backup.timer` and
`muse-taste-retrain.timer`) and a second virtualenv join the operational
surface. `SubprocessContentEmbedder` converts a dead, desynced, or
short-answered child into a `RuntimeError` — naming the exit code, the
malformed line, or the mismatched track id, depending on which check catches
it — so a missing or broken `universal_reco/.venv` fails loudly rather than
as a silent gap in `content_embeddings`. But the message is still just the
exit code or the raw line, not the child's stderr, so diagnosing *why* the
venv is broken still means running the batch command by hand.

**Bad.** `content_embed/model.py` loads MERT with `trust_remote_code=True`,
which runs third-party code capable of writing to stdout at any time — and
this is not hypothetical. The muse-side slow end-to-end test caught MERT's
own loader doing exactly that on first `embed()` call, a stray warning that
landed on stdout and corrupted `content_embed.batch`'s JSON line protocol,
which `SubprocessContentEmbedder` surfaced as a malformed-line `RuntimeError`
rather than a misattributed vector — the batch failed safely, but it
failed. `content_embed.batch.main()` now seizes the real fd 1 for the
protocol before the model or anything it imports is touched, redirecting fd
1 itself to stderr, so a future stray write lands in the journal instead of
on the wire. That closes this specific hole, but the underlying cost stands:
`trust_remote_code=True` means the checkpoint's code can do this again in a
way nothing here anticipates, and the fd swap is a defense against the
*symptom* (stdout gets written to), not the *cause* (the checkpoint's code
is trusted at all).

**Bad.** The corpus mean shifts as the library grows, so scores computed
before and after a batch are not strictly comparable. Within one ranking pass
this is invisible. Nothing caches content scores today, and nothing should
start without revisiting this.

**Binding.** `rank()` may not give `clap_sim` and `content_sim` independent
weights. They are one audio-content term with an internal split, until an A/B
says otherwise.

**Binding.** No leg may assume `content_sim` is available. A track without a
`content_vec`, or a library with no `content_stats` row, must **not**
redistribute content's share of `w_audio` outward across the other five
legs — that would shrink audio's total influence below `w_audio` on exactly
the tracks where CLAP is the only audio signal available, quietly undoing
what the previous Binding clause establishes. Instead, content's share
reverts to `clap_sim`, so the audio term stays whole at `w_audio` with CLAP
supplying all of it; only once that reversion has happened does the
ordinary null-leg renormalization — the same contract ADR 0003 already binds
`taste_sim` and the lyrics side of `emotion_vec` to — apply to the audio
term as a whole, on the rare track where `clap_sim` is also unavailable.
