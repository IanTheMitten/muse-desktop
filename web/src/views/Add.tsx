import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import {
  getJobs,
  getMbReleases,
  getMbTracks,
  queueDownload,
  retryJob,
  searchMbArtists,
  statusOf,
  type Job,
  type MbArtist,
  type MbReleaseGroup,
  type MbTrack,
  type MbTracklist,
  type SkippedTrack,
  type ReleaseKind,
} from "../api";
import {
  BackIcon,
  ChevronIcon,
  DownloadIcon,
  ErrorIcon,
  LinkIcon,
  RetryIcon,
  SearchGlyph,
} from "../components/Icons";
import { Screen } from "../components/Screen";
import { count, duration } from "../format";
import { useJobs } from "../jobs/JobsContext";
import "../styles/add.css";

/* Growing the library, without the terminal.
 *
 * One field that takes an artist name or a pasted link, then artist, releases,
 * tracks, queued. No wizard, no settings, no destination picker.
 *
 * MusicBrainz is the one browse source. Artist-first: a name search is the
 * only way in, and the field must never imply a track search. The search runs
 * on submit, never per keystroke, because MusicBrainz's 1 req/s limiter is
 * shared with downloads and art, and prefixes of a name would spend it.
 *
 * Activity is a strip at the top rather than a fifth destination. What is
 * running, what is waiting, and what failed is something you glance at on the
 * way past, not a place you go.
 *
 * No accent anywhere in here: DESIGN.md spends that budget elsewhere and LAYOUT
 * invariant 5 says rank is width and order, never colour.
 */

type Step =
  | { at: "search" }
  | { at: "artist"; artist: MbArtist; kind: ReleaseKind }
  | { at: "release"; artist: MbArtist; kind: ReleaseKind; group: MbReleaseGroup };

const KINDS: { key: ReleaseKind; label: string }[] = [
  { key: "album", label: "Albums" },
  { key: "single", label: "Singles & EPs" },
];

/** A pasted link is a different job from a typed name, and the field can tell
 *  which without asking: one of them starts with a scheme. */
const looksLikeUrl = (text: string) => /^https?:\/\//i.test(text.trim());

/** Said the same way everywhere, because it is one fact: the request left and
 *  nothing came back. Never routed through `Screen`'s error prop -- the
 *  rest of the screen is unaffected and must not be reddened. */
const NO_ANSWER = "MusicBrainz did not answer.";

/** What to say when `POST /api/jobs` refused, for either kind of row.
 *
 *  A queue that failed must never leave the row saying "queued": the screen
 *  would be stating a fact that did not happen, which is the one thing
 *  PRODUCT.md's third principle forbids. Which sentence depends on who said
 *  no -- only the MusicBrainz kinds reach MusicBrainz server-side, and only
 *  they can answer 502, so every other refusal is this library's own and
 *  says so rather than blaming a service it never called. */
const queueFailure = (cause: unknown, what: string) =>
  statusOf(cause) === 502 ? NO_ANSWER : `Could not queue ${what}.`;

/** The words for a field a MusicBrainz release turned out not to have. The
 *  server names its own columns; a person reading a screen should not have
 *  to. */
const MISSING_WORDS: Record<string, string> = {
  recording_mbid: "recording id",
  artist_name: "artist",
  artist_names: "artist",
  album_name: "album title",
  track_name: "title",
};

/** What the whole-record button says when the server queued fewer tracks
 *  than the record has. Named by what they were missing rather than by name:
 *  a title is the likeliest hole, so the ones being described are exactly
 *  the ones that may have none. */
const skippedSentence = (skipped: SkippedTrack[]) => {
  const holes = [
    ...new Set(
      skipped.flatMap((track) =>
        track.missing.map((field) => MISSING_WORDS[field] ?? field),
      ),
    ),
  ];
  return `${count(skipped.length, "track")} not queued: MusicBrainz has no ${holes.join(
    " or ",
  )} for ${skipped.length === 1 ? "it" : "them"}.`;
};

export function Add() {
  // `/add?q=` (chart and "Not in your library" cards) starts filled in and
  // already submitted, since search runs on submit here.
  const initial = useSearchParams()[0].get("q") ?? "";
  const [query, setQuery] = useState(initial);
  const [submitted, setSubmitted] = useState(looksLikeUrl(initial) ? "" : initial.trim());
  const [step, setStep] = useState<Step>({ at: "search" });
  // Null is "nothing asked yet, or still asking"; `[]` is "MusicBrainz
  // answered and has nobody by that name".
  const [artists, setArtists] = useState<MbArtist[] | null>(null);
  const [down, setDown] = useState(false);
  const [sheetOpen, setSheetOpen] = useState(false);

  const isUrl = looksLikeUrl(query);

  // Hits belong to the term that produced them, and so does a failure.
  const edit = (text: string) => {
    setQuery(text);
    setSubmitted("");
    setArtists(null);
    setDown(false);
  };

  useEffect(() => {
    if (!submitted) return;
    const abort = new AbortController();
    setArtists(null);
    setDown(false);
    searchMbArtists(submitted, abort.signal)
      .then(setArtists)
      .catch(() => {
        if (!abort.signal.aborted) setDown(true);
      });
    return () => abort.abort();
  }, [submitted]);

  const asking = submitted !== "" && artists === null && !down;

  return (
    <Screen title="Add" subtitle="Search MusicBrainz, or paste a link">
      <div className="add">
      <Activity open={sheetOpen} onOpen={() => setSheetOpen(true)} onClose={() => setSheetOpen(false)} />

      {step.at === "search" && (
        <>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              if (!isUrl && query.trim()) setSubmitted(query.trim());
            }}
          >
            <label className="field">
              <span className="field-label">Artist name, or paste a link</span>
              <span className="field-box">
                {isUrl ? <LinkIcon className="icon field-icon" /> : <SearchGlyph className="icon field-icon" />}
                <input
                  className="field-input"
                  type="search"
                  value={query}
                  onChange={(event) => edit(event.target.value)}
                  placeholder="Artist name, or paste a link"
                  autoCapitalize="off"
                  autoCorrect="off"
                  spellCheck={false}
                />
              </span>
            </label>
            {!isUrl && (
              <button className="button button-quiet" type="submit" disabled={!query.trim()}>
                Search
              </button>
            )}
          </form>

          {isUrl ? (
            <FromLink url={query} onQueued={() => edit("")} />
          ) : (
            <>
              {asking && <p className="muted">Asking MusicBrainz…</p>}
              {down && <p className="muted">{NO_ANSWER}</p>}
              {artists?.length === 0 && (
                <p className="muted">MusicBrainz has no artist by that name.</p>
              )}
              {artists !== null && artists.length > 1 && (
                <p className="add-note add-pick-note">
                  More than one artist goes by this name, and nothing here can
                  tell which one you meant. Pick.
                </p>
              )}
              {artists !== null && artists.length > 0 && (
                <p className="add-label">Artists</p>
              )}
              <ul className="rows">
                {artists?.map((artist) => (
                  <li key={artist.mbid}>
                    <button
                      className="row"
                      onClick={() => setStep({ at: "artist", artist, kind: KINDS[0].key })}
                    >
                      <span className="row-main">
                        <span className="row-title">{artist.name}</span>
                        {/* The owned count is the only thing on the row that
                            tells same-named artists apart. */}
                        <span className="row-sub">
                          {`${artist.disambiguation || "no description in MusicBrainz"} · ${
                            artist.in_library > 0
                              ? `${count(artist.in_library, "track")} in library`
                              : "not in library"
                          }`}
                        </span>
                      </span>
                      <ChevronIcon className="icon icon-sm" />
                    </button>
                  </li>
                ))}
              </ul>

              <p className="add-note">
                Artist first. Names go to MusicBrainz as typed, in any script.
              </p>
            </>
          )}
        </>
      )}

      {step.at === "artist" && (
        <>
          <button className="add-back" onClick={() => setStep({ at: "search" })}>
            <BackIcon className="icon icon-sm" /> Add
          </button>
          <h2 className="add-title">{step.artist.name}</h2>
          <p className="add-sub">{step.artist.disambiguation || "From MusicBrainz"}</p>

          <div className="seg" role="group" aria-label="Release type">
            {KINDS.map((option) => (
              <button
                key={option.key}
                className="seg-button"
                aria-pressed={step.kind === option.key}
                onClick={() => setStep({ ...step, kind: option.key })}
              >
                {option.label}
              </button>
            ))}
          </div>

          <MbReleases
            mbid={step.artist.mbid}
            kind={step.kind}
            onOpen={(group) => setStep({ ...step, at: "release", group })}
          />
        </>
      )}

      {step.at === "release" && (
        <MbTracklistView
          group={step.group}
          backName={step.artist.name}
          onBack={() => setStep({ at: "artist", artist: step.artist, kind: step.kind })}
        />
      )}
      </div>
    </Screen>
  );
}

/** One artist's release groups, newest first.
 *
 *  Empty and unreachable are different answers and the endpoint keeps them
 *  apart on purpose -- `200 []` is MusicBrainz saying there is nothing,
 *  `502` is MusicBrainz saying nothing. Two sentences, not one. */
function MbReleases({
  mbid,
  kind,
  onOpen,
}: {
  mbid: string;
  kind: ReleaseKind;
  onOpen: (group: MbReleaseGroup) => void;
}) {
  const [groups, setGroups] = useState<MbReleaseGroup[] | null>(null);
  const [down, setDown] = useState(false);

  useEffect(() => {
    const abort = new AbortController();
    setGroups(null);
    setDown(false);
    getMbReleases(mbid, kind, abort.signal)
      .then(setGroups)
      .catch(() => {
        if (!abort.signal.aborted) setDown(true);
      });
    return () => abort.abort();
  }, [mbid, kind]);

  if (down) return <p className="muted">{NO_ANSWER}</p>;
  if (groups === null) return <p className="muted">Asking MusicBrainz…</p>;
  if (groups.length === 0) {
    return (
      <p className="muted">
        {kind === "album"
          ? "MusicBrainz lists no albums for this artist."
          : "MusicBrainz lists no singles or EPs for this artist."}
      </p>
    );
  }

  return (
    <ul className="rows">
      {groups.map((group) => (
        <li key={group.mbid}>
          <button className="row release" onClick={() => onOpen(group)}>
            <span className="release-year num">
              {group.release_date.slice(0, 4) || "-"}
            </span>
            <span className="row-main">
              <span className="row-title">{group.title}</span>
              {/* A word, only where it is not an album. No tabs over four
                  rows, and no badge. */}
              {group.primary_type && group.primary_type !== "Album" && (
                <span className="row-sub">
                  {group.primary_type.toLowerCase()}
                </span>
              )}
            </span>
            <ChevronIcon className="icon icon-sm" />
          </button>
        </li>
      ))}
    </ul>
  );
}

/** A release group's tracklist. The pressing was chosen server-side, so there
 *  is nothing to pick here -- only what is already held, and what is not.
 *
 *  Every field a job needs is already on each row: the endpoint flattens the
 *  release's facts onto every track precisely so no client composes two
 *  objects, and `{...track, ...release}` -- the natural way round to write
 *  that -- is the compilation bug this codebase has already fixed twice. So
 *  the payload below is copied field by field off one row and nothing else. */
function MbTracklistView({
  group,
  backName,
  onBack,
}: {
  group: MbReleaseGroup;
  backName: string;
  onBack: () => void;
}) {
  const [list, setList] = useState<MbTracklist | null>(null);
  const [down, setDown] = useState(false);
  const [queued, setQueued] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  useEffect(() => {
    const abort = new AbortController();
    setList(null);
    setDown(false);
    getMbTracks(group.mbid, abort.signal)
      .then(setList)
      .catch(() => {
        if (!abort.signal.aborted) setDown(true);
      });
    return () => abort.abort();
  }, [group.mbid]);

  const tracks = list?.tracks ?? [];
  const held = useMemo(
    () => tracks.filter((track) => track.in_library).length,
    [tracks],
  );
  const missing = useMemo(
    () => tracks.filter((track) => !track.in_library),
    [tracks],
  );

  const queueOne = async (track: MbTrack) => {
    setQueued((was) => new Set(was).add(track.recording_mbid));
    setFailure(null);
    try {
      await queueDownload({
        kind: "mb",
        recording_mbid: track.recording_mbid,
        release_mbid: track.release_mbid,
        artist_name: track.artist_name,
        artist_names: track.artist_names,
        album_name: track.album_name,
        track_name: track.track_name,
        track_no: track.track_no,
        disc_no: track.disc_no,
        total_tracks: track.total_tracks,
        duration_ms: track.duration_ms,
        isrc: track.isrc,
      });
    } catch (cause) {
      // The row goes back to offering the download. Leaving it "queued" over
      // a job that was never created is the screen lying about the one thing
      // it is here to report.
      setQueued((was) => {
        const still = new Set(was);
        still.delete(track.recording_mbid);
        return still;
      });
      setFailure(
        queueFailure(
          cause,
          track.track_name ? `“${track.track_name}”` : "that track",
        ),
      );
    }
  };

  // The whole record. The server re-reads the release rather than trusting a
  // list of rows, so this one can fail where the tracklist above succeeded.
  const queueAll = async () => {
    setBusy(true);
    setFailure(null);
    try {
      const { skipped } = await queueDownload({
        kind: "mb_release",
        release_group_mbid: group.mbid,
      });
      // Only what the server actually took. Marking a skipped track "queued"
      // would be the same lie `queueOne` stopped telling, one route along.
      const notTaken = new Set(skipped.map((track) => track.recording_mbid));
      setQueued(
        new Set(
          missing
            .map((track) => track.recording_mbid)
            .filter((mbid) => !notTaken.has(mbid)),
        ),
      );
      if (skipped.length > 0) setFailure(skippedSentence(skipped));
    } catch (cause) {
      setFailure(queueFailure(cause, "this record"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <button className="add-back" onClick={onBack}>
        <BackIcon className="icon icon-sm" /> {backName}
      </button>
      <h2 className="add-title">{group.title}</h2>
      {/* The year is all this line can say until the tracklist lands. Counting
          before it arrives would have the header still reading while the line
          under it says MusicBrainz did not answer. */}
      <p className="add-sub num">
        {group.release_date.slice(0, 4) || "-"}
        {list &&
          ` · ${count(tracks.length, "track")} · ${
            held === 0
              ? "none in library"
              : held >= tracks.length
                ? "all in library"
                : `${held} in library`
          }`}
      </p>

      {down && <p className="muted">{NO_ANSWER}</p>}

      {list && (
        <button
          className="button button-primary"
          onClick={queueAll}
          disabled={busy || missing.length === 0}
        >
          <DownloadIcon className="icon icon-sm" />
          {/* The number the Activity strip is about to show, not the number
              missing. This posts `mb_release`, which queues every track of
              the release: the ones already held short-circuit harmlessly at
              `find_done`, but they are still job rows and they still appear.
              A label counting only what is missing promises less than it
              does, and is contradicted a second later by the strip. How much
              is already held is on the line above this one. */}
          {missing.length === 0
            ? "Everything here is downloaded"
            : `Download all ${count(tracks.length, "track")}`}
        </button>
      )}

      {failure && <p className="muted">{failure}</p>}

      <ul className="rows">
        {tracks.map((track) => (
          <li key={`${track.disc_no}-${track.track_no}-${track.recording_mbid}`}>
            <span className="row">
              <span className="row-no num">{track.track_no ?? "-"}</span>
              <span className="row-main">
                <span className="row-title">{track.track_name}</span>
              </span>
              <span className="row-time num">
                {duration(track.duration_ms ?? 0)}
              </span>
              {/* Flagged before the tap, not after: `in_library` comes down
                  with the row, joined on `recording_mbid`. */}
              {track.in_library ? (
                <span className="add-have num">in library</span>
              ) : (
                <button
                  className="add-get"
                  onClick={() => queueOne(track)}
                  disabled={queued.has(track.recording_mbid)}
                  aria-label={`Download ${track.track_name}`}
                >
                  {queued.has(track.recording_mbid) ? (
                    <span className="num">queued</span>
                  ) : (
                    <DownloadIcon className="icon icon-sm" />
                  )}
                </button>
              )}
            </span>
          </li>
        ))}
      </ul>
    </>
  );
}

/** For a recording MusicBrainz does not list. The metadata has to come from
 *  somewhere, so it is asked for rather than guessed at from the page title. */
function FromLink({ url, onQueued }: { url: string; onQueued: () => void }) {
  const [artist, setArtist] = useState("");
  const [track, setTrack] = useState("");
  const [album, setAlbum] = useState("");
  const [busy, setBusy] = useState(false);

  const ready = artist.trim() !== "" && track.trim() !== "";

  return (
    <form
      className="add-link"
      onSubmit={async (event) => {
        event.preventDefault();
        if (!ready) return;
        setBusy(true);
        try {
          await queueDownload({
            kind: "url",
            url: url.trim(),
            artist_name: artist.trim(),
            track_name: track.trim(),
            ...(album.trim() ? { album_name: album.trim() } : {}),
          });
          setArtist("");
          setTrack("");
          setAlbum("");
          onQueued();
        } finally {
          setBusy(false);
        }
      }}
    >
      <p className="add-label">One track from a link</p>

      <label className="field">
        <span className="field-label">Artist</span>
        <span className="field-box">
          <input
            className="field-input"
            value={artist}
            onChange={(event) => setArtist(event.target.value)}
            required
          />
        </span>
      </label>

      <label className="field">
        <span className="field-label">Track</span>
        <span className="field-box">
          <input
            className="field-input"
            value={track}
            onChange={(event) => setTrack(event.target.value)}
            required
          />
        </span>
      </label>

      <label className="field">
        <span className="field-label">Album (defaults to Singles)</span>
        <span className="field-box">
          <input
            className="field-input"
            value={album}
            onChange={(event) => setAlbum(event.target.value)}
            placeholder="Singles"
          />
        </span>
      </label>

      <button className="button button-primary" type="submit" disabled={!ready || busy}>
        <DownloadIcon className="icon icon-sm" /> Download
      </button>

      <p className="add-note">
        For a recording MusicBrainz does not list.
      </p>
    </form>
  );
}

/** The strip, and the sheet behind it. Not a destination: what is running is
 *  something you glance at on the way past. */
function Activity({
  open,
  onOpen,
  onClose,
}: {
  open: boolean;
  onOpen: () => void;
  onClose: () => void;
}) {
  const { job: lastEvent } = useJobs();
  const [jobs, setJobs] = useState<Job[]>([]);

  // Re-read on every job event rather than polling: the SSE stream already
  // says when something changed.
  useEffect(() => {
    const abort = new AbortController();
    getJobs(40, abort.signal).then(setJobs).catch(() => {});
    return () => abort.abort();
  }, [lastEvent]);

  const downloads = jobs.filter((job) => job.kind === "download");
  const running = downloads.filter((job) => job.state === "running");
  const waiting = downloads.filter((job) => job.state === "queued");
  const failed = downloads.filter((job) => job.state === "error");

  if (downloads.length === 0) return null;

  const summary = [
    running.length > 0 && `${running.length} downloading`,
    waiting.length > 0 && `${count(waiting.length, "waiting")}`,
    failed.length > 0 && `${failed.length} failed`,
  ]
    .filter(Boolean)
    .join(", ");

  return (
    <>
      <button className="strip" onClick={onOpen}>
        <span className="strip-text">
          <span>{summary || "Nothing running"}</span>
          <span className="strip-sub">
            {running[0]?.message ?? "Recently finished"}
          </span>
        </span>
        <ChevronIcon className="icon icon-sm" />
      </button>

      {open && (
        <div className="activity">
          <div className="activity-head">
            <h2 className="add-title">Activity</h2>
            <button className="add-back" onClick={onClose}>
              Close
            </button>
          </div>
          {downloads.slice(0, 20).map((job) => (
            <JobRow key={job.id} job={job} />
          ))}
        </div>
      )}
    </>
  );
}

/** yt-dlp's errors are not readable. The real one for an age-gated video is
 *  three hundred characters of cookie-export documentation, so the card leads
 *  with the sentence that says what to do and keeps the raw string underneath,
 *  because the raw string is what you paste into a search when the sentence is
 *  not enough. */
function humanError(message: string | null): string {
  if (!message) return "It failed, with nothing to say about why.";
  if (/Sign in to confirm/i.test(message)) {
    return "YouTube wants a signed-in session for this one.";
  }
  if (/not available|unavailable|Private video/i.test(message)) {
    return "The video this was matched to is gone.";
  }
  if (/HTTP Error 429|rate/i.test(message)) return "Rate limited. Try later.";
  if (/No such file|Permission denied/i.test(message)) {
    return "The file could not be written.";
  }
  return "It failed on the way down.";
}

function JobRow({ job }: { job: Job }) {
  const [retried, setRetried] = useState(false);
  const name =
    (job.payload.track_name as string | undefined) ??
    (job.payload.url as string | undefined) ??
    `Track ${job.payload.track_rowid ?? "?"}`;
  const artist = job.payload.artist_name as string | undefined;

  return (
    <div className="job">
      {job.state === "error" ? (
        <ErrorIcon className="icon icon-sm job-bad" />
      ) : (
        <DownloadIcon className="icon icon-sm job-mark" />
      )}
      <div className="job-main">
        <span className="job-name">{name}</span>
        <span className="job-step">
          {artist ? `${artist} · ` : ""}
          {job.state === "running" ? (job.message ?? "working") : job.state}
        </span>
        {job.state === "error" && (
          <>
            <span className="job-error">{humanError(job.message)}</span>
            <details className="job-raw">
              <summary>Details</summary>
              <span className="num">{job.message}</span>
            </details>
          </>
        )}
      </div>
      {job.state === "error" && (
        <button
          className="button button-quiet"
          disabled={retried}
          onClick={async () => {
            setRetried(true);
            await retryJob(job.id);
          }}
        >
          <RetryIcon className="icon icon-sm" /> {retried ? "Queued" : "Retry"}
        </button>
      )}
    </div>
  );
}
