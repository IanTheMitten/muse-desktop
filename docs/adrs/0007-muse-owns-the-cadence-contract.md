# ADR 0007: Muse owns the Cadence contract

- **Status:** Accepted
- **Date:** 2026-10-01
- **Slice:** n/a (Cadence integration)
- **Related:** `docs/contracts/cadence.md`,
  `docs/adrs/0001-plays-stores-physical-fact.md`,
  `~/Projects/Cadence_Speaker/docs/specs/2026-10-01-cadence-decision-harness-v1.md`

## Context

Cadence, the home voice system, consumes a fixed subset of Muse's `/api`: the
Cadence surface. Muse's own client is the web app in this repo, so a route
can change shape for the web client's sake and nothing in Muse's test suite
notices that a second caller broke. The requirement is that neither project
breaks the other without knowing: a Muse change that would break Cadence
fails here, and a Cadence that meets a Muse it does not understand degrades
(music off) rather than crashes.

## Decision

**Muse owns the contract.** It is four things in this repo: the doc
`docs/contracts/cadence.md`, the committed snapshot
`docs/contracts/cadence.openapi.json`, the tests (`tests/test_contract.py`,
`tests/test_contract_shapes.py`), and `GET /api/contract`, which serves the
version.

**Versioning.** `MAJOR.MINOR`. Additive changes (a new optional parameter, a
new response field, a new operation) are a minor bump. A rename, a removal, a
type change, or a change of meaning is a major bump.

**A major bump is expand, then contract.** Cadence disables music on any
major other than `1`, so a Muse that bumps to `2.0` while still serving the
old behaviour still silences Cadence. The deprecation window is: Muse ships
the replacement alongside the old behaviour as a minor bump and marks the old
one deprecated; Cadence moves to the replacement and widens its accepted
majors to include the new one; only then does Muse remove the old behaviour
as the new major. The window closes only after that Cadence commit, so a
major bump needs a change on Cadence's side as well.

**Muse-first change flow.** Code, `CONTRACT_VERSION`, snapshot, and doc
change in one Muse commit; then Cadence syncs its fixtures. `--write`
refuses to rewrite the snapshot when the surface changed and the version did
not.

**The `ended_reason` set is major.** Adding or renaming one of the six values
is a major change, pinned by `tests/test_contract.py`. `plays` stores the
client's reason verbatim and never an interpretation (ADR 0001), so the set
is the one part of the write path Cadence's meaning depends on.

## Rejected alternatives

- **Cadence owns the contract.** The party that can break it is Muse, and
  only Muse's tests run when a Muse route changes.
- **Fixtures only, with no version.** Cadence's copy of the OpenAPI would
  catch drift only when someone refreshed it, and a Cadence that met a
  changed Muse would have no signal to degrade on.
- **`response_model`s on the surface routes.** Every surface route returns a
  plain `dict`, so OpenAPI says only "an object" and cannot pin the fields
  Cadence reads. FastAPI filters a response through its model, which would
  strip fields the web client reads. `tests/test_contract_shapes.py` asserts
  the fields against a real app instead. `GET /api/contract` is new, so it
  does have a response model.
- **Pinning `/api/dashboard` and `/api/player/state` now.** No Cadence
  component calls them in v1; pinning would freeze the home screen's payload
  and the phone's player state for a caller that does not exist. Wiring one
  later is a minor bump that adds it to the surface.

## Consequences

**Good.** A change that would break Cadence fails `uv run pytest` in Muse,
before it ships, and the failure names the rule.

**Bad.** The snapshot drops `description`, `summary`, `title`, and
`operationId`, so it cannot see a change of meaning behind an unchanged
shape (`score` flipping direction, say). That judgment is on the person
making the change, who must bump the major.

**Binding.** No change to the Cadence surface merges unless the snapshot,
the doc, and `CONTRACT_VERSION` move together.
