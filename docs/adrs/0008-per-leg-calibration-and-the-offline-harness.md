# ADR 0008: Every leg is calibrated through its own CDF before fusion, and an offline harness measures the recommender

- **Status:** Accepted
- **Date:** 2026-10-09
- **Slice:** 2 (Recommender)
- **Related:** `docs/adrs/0003-emotion-and-taste-legs-in-the-recommender.md`,
  `docs/adrs/0004-content-embed-leg-and-the-shared-audio-budget.md`,
  PRD open question O8, `docs/notes/2026-10-09-reco-calibration-eval.md`

## Context

`rank()` scores a candidate as the weighted average of the legs it has,
renormalizing the weights over whichever are present. That is only fair if
every leg's similarities are on the same scale, and they are not. Over 4,000
random track pairs on the live library (2026-10-09):

| leg | median | p5–p95 |
|---|---|---|
| content (already percentile-rescaled, ADR 0004) | 0.46 | 0.11–0.86 |
| audio (CLAP) | 0.55 | 0.25–0.81 |
| lyrics | 0.44 | 0.20–0.60 |
| features | 0.87 | 0.68–0.98 |
| emotion | 0.86 | 0.69–0.96 |
| taste (49 tracks) | 0.00 | −0.09–0.17 |

So the *set* of legs a candidate carries moved its score by itself: a
post-freeze track (no features) lost a leg that gives nearly everyone 0.87,
an instrumental shed one that gives everyone 0.44, and a well-played
candidate under a well-played seed spent 20% of its weight on a taste score
near zero that unplayed candidates skipped. It also meant features and
emotion barely discriminated at all.

Nothing could say whether a change to this helped: O8 asks for offline
recall on held-out transitions before any scoring change, and none existed.

## Decision

**An offline harness, `muse/reco_eval.py`** (`uv run python -m
muse.reco_eval`). Consecutive plays within 30 minutes form a transition;
each is replayed through the same retrieve-then-fuse path `recommend_for`
runs, ranking the full retrieval union (no sampled negatives — Krichene &
Rendle, KDD 2020, show sampled metrics can reverse comparisons). It reports
hit@{1,5,10,20}, MRR, and retrieval recall separately for next tracks played
through, the subset not queued by radio, and next tracks skipped (lower is
better). Read-only; records no offers. Ratings, freshness, and spacing are
left out, being request-time constraints rather than evidence.

**Per-leg calibration.** Each leg's similarity is mapped through the
empirical CDF of that leg over random track pairs (101 quantile knots,
2,000 seeded pairs, linear interpolation). A typical pair then scores 0.5 on
every leg, so a leg dropping out of the renormalized average no longer
biases the result. The math (`fit_calibration`, `calibrate`) is pure and
lives in `reco.py`; fitting from vectors lives in `recommend.py`
(`calibration_for`), cached per database on a fingerprint of the three vector
tables and the content statistics — the vectors' own stamps, because the
embed batch, the content batch, and the nightly taste retrain rewrite them
under a long-lived process. Audio's two legs are each calibrated before ADR
0004's split combines them. A leg with fewer than 100 pairs stays raw.

The fusion weights are **not** changed.

## Evidence

Baseline and result on the live library, 2026-10-09 (606 transitions, 126
played through, 401 skipped):

| variant | played-through MRR | hit@10 | skipped MRR ↓ | skipped hit@1 ↓ |
|---|---|---|---|---|
| raw (before) | 0.046 | 0.103 | 0.031 | 0.015 |
| raw, no taste | 0.054 | 0.111 | 0.040 | 0.020 |
| calibrated (now) | 0.038 | 0.111 | 0.019 | 0.002 |
| calibrated, no taste | 0.042 | 0.111 | 0.019 | 0.002 |

On tracks played through, calibration is neutral within noise (one
transition is 0.008 of hit@k). On skipped tracks it is the clearest
difference in the table: tracks the listener rejected rank lower. Chance
hit@10 is ~0.009.

## Consequences

- Leg sims in the reasons payload are calibrated percentiles, not raw
  cosines, and `reasons.calibrated` says which.
- Archetype profiles score through the same knots, so the curator compares
  one scale. `profiles.SCORING_VERSION` (now 2) is folded into each row's
  `seed_hash` stamp, so rows computed under the raw fusion read stale once
  and are recomputed rather than served on the old scale.
- Calibrated candidate sets are ~1.5x more spread out (150 random seeds:
  top-1 to top-10 gap 0.053 -> 0.077, to top-20 0.079 -> 0.120). The
  additive freshness boost would have been ~1/3 weaker relative to fit, so
  `FRESHNESS_MAX` goes from 0.15 to 0.23 (15% x 1.52) to keep its pull, and
  PRD O2's cap is amended to match. It still cannot overturn a 0.4 gap.
- **Retrieval, not fusion, is the ceiling.** Only 41% of played-through next
  tracks (30% of those not queued by radio) are in the top-30-per-leg union
  at all, so no fusion change can reach the other 59%.
- The taste rows leak (ALS is trained on the replayed transitions) and still
  score no better than taste off. Taste is not helping yet at 49 tracks.
- The client has tagged radio picks `source='radio'` since 2026-09-03, so
  the 630 `queue` plays (2026-08-10 onward) are mostly real listener
  choices -- radio did not exist before that date. The tagging set lived only
  in the tab, though, so a reload or a device switch tagged restored radio
  picks `queue`. `player_state.radio_added` (migration 16) now carries it.
  Plays already recorded stay as recorded (ADR 0001).
