# The Cadence contract

## What this is

Cadence is the home voice system (ESP32 speaker). It calls a fixed subset of
Muse's `/api`, called the **Cadence surface**. Two Cadence components call it:
the music plugin (search, vibe search, ratings, plays) and
`tools/muse_proxy.py` (the audio stream, transcoded to PCM through ffmpeg).

Muse owns the contract. It lives here, in the Muse repo, and a change to the
surface lands in Muse first. The rule and its reasons are in
`docs/adrs/0007-muse-owns-the-cadence-contract.md`.

Cadence reaches Muse at `100.122.149.75:8080`. Muse binds tailnet addresses
only (`muse/net.py`), so the surface is never on the public internet.

## Version

The current version is `1.0`. `GET /api/contract` returns it:

```json
{"version": "1.0"}
```

Cadence reads it at startup:

- `404` means a pre-contract Muse. Cadence accepts it as the baseline.
- Major `1` is accepted.
- Any other major disables music in Cadence, which says "Muse isn't
  answering". It does not crash.
- Unknown response fields are ignored, so adding a field is always safe.

The version lives in `CONTRACT_VERSION` in `muse/contract.py`. The committed
snapshot of the surface, `docs/contracts/cadence.openapi.json`, records the
same version.

## The surface

Eight operations. Every one except the stream returns JSON.

### `GET /api/contract`

No parameters. `200` with `{"version": "<major>.<minor>"}`.

### `GET /api/library/search`

Parameters: `q` (string, default empty), `limit` (integer, default 60).
`200` with `{"q": ..., "results": [...]}`. Cadence reads, per result:

| Field | Type |
|---|---|
| `track_rowid` | integer |
| `track_name` | string |
| `artist_name` | string |
| `artist_names` | list of strings |
| `duration_ms` | integer or `null` |
| `playable` | boolean |

`duration_ms` may be `null`. `playable` is true when the track's status is
`done`; a track whose file has gone missing is still returned, as unplayable.

### `GET /api/library/search/vibe`

Parameters: `q` (string, default empty), `n` (integer, default 20). The query
is a text description of a sound, embedded with CLAP and compared against each
track's stored audio vector. An empty or blank `q` returns `200` with an empty
`results` list.

`200` with `{"q": ..., "results": [...]}`. Cadence reads, per result:

| Field | Type |
|---|---|
| `track_rowid` | integer |
| `score` | float |
| `track` | object with the same six fields as a search result |

`score` is cosine similarity between the text and the track's audio vector.
Higher is closer. Results come sorted by `score`, best first.

`503` when the `embed` extra is not installed on the Muse machine. Vibe search
is the only route that needs it.

### `POST /api/plays`

Opens a play span. Body: `{"track_rowid": int, "duration_ms": int, "source":
string | null}`. Cadence sends `source` as `"cadence"`.

`201` with `{"id": <integer>}`. Cadence reads `id`.

### `PATCH /api/plays/{play_id}`

Heartbeat for an open span. Body: `{"position_ms": int, "seek_count": int |
null}`. `200` with `{"ok": true}`. See "Play spans" for why this is required.

### `POST /api/plays/{play_id}/close`

Closes a span. Body: `{"ended_reason": string, "position_ms": int | null}`.
`200` with `{"ok": true}`. `422` when `ended_reason` is not one of the six
values below. Cadence reads the status only.

### `POST /api/ratings`

Body: `{"track_rowid": int, "rating": 1 | -1 | null}`. `null` clears the
rating. `200` with `{"track_rowid": ..., "rating": ...}`. `404` for an unknown
`track_rowid`. `422` for any other `rating` value. Cadence reads the status
only.

### `GET /api/tracks/{track_rowid}/stream`

The audio file. Body is `audio/mp4`. Honours `Range` requests: `200` for the
whole file, `206` for a satisfiable range, `416` for an unsatisfiable one.
`404` for an unknown track, a track never downloaded, or a file that has gone
missing from disk. Cadence reads the body.

## Play spans

A play span is one row in `plays`. Muse records `ended_reason` exactly as the
client reports it and never turns it into a derived "skipped" flag, because
`plays` is the one table that cannot be regenerated
(`docs/adrs/0001-plays-stores-physical-fact.md`).

The six `ended_reason` values are exactly `ENDED_REASONS` in `muse/api.py`.
Cadence's meaning for each:

| Value | Cadence sends it when |
|---|---|
| `track_end` | the track played to its end |
| `user_next` | the user asked for the next track |
| `user_prev` | the user asked for the previous track |
| `new_selection` | the user chose something else to play |
| `pause_timeout` | playback was paused for more than 600 s |
| `lost` | the session dropped or the stream failed |

Cadence maps "stop the music" to a pause. A pause longer than 600 s closes the
span with `pause_timeout`.

**Heartbeat rule.** While a span is open, Cadence must `PATCH` it at least
every 30 s, paused time included. Muse's sweeper runs every 30 s
(`SWEEP_INTERVAL_SECONDS` in `muse/api.py`) and `sweep_stale` in
`muse/library/plays.py` closes any open span whose last heartbeat is more
than 60 s old, as `lost`. One missed heartbeat is tolerated; two are not. The
web client heartbeats every 10 s.

A Cadence that only opens and closes would have every play longer than about
90 s closed as `lost` by the sweeper, with no reached position recorded. The
span would then be closed, so Cadence's own `track_end` or `pause_timeout`
would be dropped. A paused span needs heartbeats too, or the 600 s
`pause_timeout` can never land.

**Closing twice keeps the first reason.** `close_span` updates only a span
that is still open. A second close, whatever its reason, changes nothing.

**Adding or renaming a reason is a major change.** `tests/test_contract.py`
pins the set.

## Not on the surface

These routes exist and are not under contract. Cadence does not call them in
v1, and Muse may change them without a version bump:

- `GET /api/recommend/{track_rowid}`
- `GET /api/player/state` and `PUT /api/player/state`
- `GET /api/dashboard`

The last three, with recommend, are what the Muse MCP server in the Cadence
repo would call. It is not wired in v1. Wiring one later is a minor bump that
adds the operation to the surface.

Cadence and Muse share facts, not state. The shared facts are plays and
ratings. Cadence keeps the speaker's own queue and never writes
`/api/player/state`, so the phone and the speaker cannot take over each
other's playback.

## What counts as a change

**Minor** (bump the minor, for example `1.0` to `1.1`):

- a new optional request parameter or body field
- a new response field
- a new operation added to the surface

**Major** (bump the major, for example `1.1` to `2.0`):

- renaming or removing a path, parameter, body field, or response field
- changing a field's type
- changing what a field or an operation means
- changing a status code Cadence depends on
- adding or renaming an `ended_reason`

Different ranking quality from the same route is not a change. Changing
`score`'s range or direction is.

Docstring and name edits that leave the normalized OpenAPI the same are not a
change: the snapshot drops `description`, `summary`, `title`, and
`operationId`.

## Changing the surface

Muse first, then Cadence.

1. Edit the code.
2. Bump `CONTRACT_VERSION` in `muse/contract.py`.
3. Run `uv run python -m muse.contract --write`. It refuses to rewrite the
   snapshot when the surface changed and the version did not.
4. Update this doc and add a row to the changelog.
5. Run `uv run pytest`.
6. Commit the code, snapshot, doc, and version together.
7. Restart `muse.service`, so the running process serves the new surface.
8. Sync Cadence: refresh its fixtures in `harness/tests/fixtures/muse/` and
   adjust its side.

A major bump is expand, then contract. Cadence disables music on any major
other than `1`, so a Muse that bumps to `2.0` while still serving the old
behaviour still silences Cadence. The deprecation window therefore runs:

1. Muse ships the replacement alongside the old behaviour as a minor bump and
   marks the old behaviour deprecated in this doc.
2. Cadence moves to the replacement and starts accepting the new major. This
   is a Cadence-side change to the set of majors it accepts.
3. Muse removes the old behaviour as the new major (`2.0`).

The window closes only after a Cadence commit accepts the new major.

## Tests

- `tests/test_contract.py` diffs the live app's normalized OpenAPI for the
  eight operations against `docs/contracts/cadence.openapi.json`. It catches
  paths, parameters, request bodies, and status codes changing. It also checks
  that the snapshot and `CONTRACT_VERSION` agree, that `--write` refuses an
  unbumped surface change, that `ENDED_REASONS` is the six values, and that
  this doc's changelog names the current version.
- `tests/test_contract_shapes.py` runs a real app and asserts the response
  fields and types Cadence reads, plus the status codes. OpenAPI cannot catch
  these: every surface route but `GET /api/contract` returns a plain `dict`,
  so its response schema is an open object. Extra fields are allowed.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0 | 2026-10-01 | Initial contract: 8 operations. |
