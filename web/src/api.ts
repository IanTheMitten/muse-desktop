import { apiFetch } from "./token";
import { radioBody, radioPath, type RadioQuery } from "./player/radio";
/* The one place that knows the API exists. */

export type Health = {
  schema_version: number;
  tracks: {
    playable: number;
    missing_file: number;
    error: number;
    total: number;
  };
  albums: number;
  artists: number;
  runtime_hours: number;
  art_coverage: number;
  lyrics_coverage: number;
  plays: number;
};

export type Artist = {
  artist_name: string;
  track_count: number;
  album_count: number;
  playable_count: number;
  /** One album of theirs that has art, so the gallery has something to draw.
   *  Null for an artist whose covers have all failed to backfill. */
  art_album_rowid: number | null;
  has_image: boolean;
};

export type Album = {
  album_rowid: number;
  album_name: string;
  track_count: number;
  playable_count: number;
  runtime_ms: number;
  has_art: boolean;
};

export type Track = {
  track_rowid: number;
  track_name: string;
  artist_name: string;
  artist_names: string[];
  album_name: string;
  album_rowid: number | null;
  track_no: number | null;
  disc_no: number | null;
  duration_ms: number | null;
  status: "done" | "missing_file" | "error" | "removed";
  is_instrumental: boolean;
  has_art: boolean;
  has_lyrics: boolean;
  gain_db: number | null;
  format: string | null;
  year: number | null;
  /** The only explicit signal in the system. Null means unrated, which is not
   *  the same as indifferent. */
  rating: -1 | 1 | null;
  /** False when the file has gone missing: shown, but never offered. */
  playable: boolean;
};

export type Rating = -1 | 1 | null;

export type AlbumDetail = {
  album_rowid: number;
  album_name: string;
  artist_name: string;
  track_count: number;
  runtime_ms: number;
  tracks: Track[];
};

/** A request the API refused, carrying the status as a number.
 *
 *  The status is load-bearing where a route separates its answers: a 502 from
 *  `/discover/...` means MusicBrainz did not answer, which is a different
 *  sentence from an empty result. Reading the number back out of a message
 *  string, a format nothing enforced, would leave that choice one edit away
 *  from picking the wrong sentence.
 *
 *  `message` stays readable, because most callers do nothing with a failure
 *  except show it. */
export class ApiError extends Error {
  readonly status: number;

  constructor(path: string, status: number, statusText?: string, detail?: string) {
    super(detail || `${path} -> ${status}${statusText ? ` ${statusText}` : ""}`);
    this.name = "ApiError";
    this.status = status;
  }
}

/** The status a request failed with, or null when it never got one -- an
 *  abort, a dead tailnet, a body that would not parse. Null is "no answer to
 *  read", never "an answer worth branching on". */
export const statusOf = (cause: unknown): number | null =>
  cause instanceof ApiError ? cause.status : null;

export async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await apiFetch(`/api${path}`, { signal });
  if (!response.ok) {
    throw new ApiError(path, response.status, response.statusText);
  }
  return (await response.json()) as T;
}

async function refusal(path: string, response: Response): Promise<ApiError> {
  let detail: string | undefined;
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") detail = body.detail;
  } catch {
    /* no JSON body: fall back to the status line */
  }
  return new ApiError(path, response.status, response.statusText, detail);
}

async function send<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await apiFetch(`/api${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw await refusal(path, response);
  return (await response.json()) as T;
}

export type Settings = {
  music_folders: string[];
  downloader_unlocked: boolean;
  folder_status: { path: string; present: boolean; songs: number }[];
  downloads: { path: string; songs: number } | null;
  downloader: { installed: boolean; install_state: string | null; update_failed: boolean };
};

export const getSettings = (signal?: AbortSignal) =>
  get<Settings>("/settings", signal);

export const unlockDownloader = (password: string): Promise<Settings> =>
  send("POST", "/settings/downloader", { password });

export const lockDownloader = (): Promise<Settings> =>
  send("DELETE", "/settings/downloader");

export async function installDownloader(): Promise<void> {
  const response = await apiFetch("/api/downloader/install", { method: "POST" });
  if (!response.ok) throw await refusal("/downloader/install", response);
}

export type UpdateInfo = { current: string; latest: string | null; url?: string };

export const getUpdate = (signal?: AbortSignal) => get<UpdateInfo>("/update", signal);

export async function putMusicFolders(folders: string[]): Promise<Settings> {
  const path = "/settings/music_folders";
  const response = await apiFetch(`/api${path}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ folders }),
  });
  if (!response.ok) throw await refusal(path, response);
  return (await response.json()) as Settings;
}

export type ImportResult = { folders_added: number; songs_added: number; already: number; ignored: number };

export async function importPaths(paths: string[]): Promise<ImportResult> {
  const path = "/library/import";
  const response = await apiFetch(`/api${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ paths }),
  });
  if (!response.ok) throw await refusal(path, response);
  return (await response.json()) as ImportResult;
}

export async function startScan(): Promise<void> {
  const response = await apiFetch("/api/scan", { method: "POST" });
  if (!response.ok) throw await refusal("/scan", response);
}

export type SmartStatus = {
  state: "installed" | "not_installed" | "unsupported";
  download_bytes: number;
  job: Job | null;
  energy_ready?: boolean;
};

export const getSmartStatus = (signal?: AbortSignal) =>
  get<SmartStatus>("/smart", signal);

export const installSmartFeatures = () =>
  send<{ job_id: number }>("POST", "/smart/install");

export const getHealth =(signal?: AbortSignal) =>
  get<Health>("/health", signal);

export const getArtists = (signal?: AbortSignal) =>
  get<Artist[]>("/library/artists", signal);

/** Every album at once. The browse view holds the whole set and filters it
 *  locally, which is what makes typing and switching segments instant. */
export const getAlbums = (signal?: AbortSignal) =>
  get<LibraryAlbum[]>("/library/albums", signal);

export type SongSort = "title" | "added" | "plays" | "year" | "duration";

export const getSongs = (
  signal?: AbortSignal,
  sort: SongSort = "title",
  order: "asc" | "desc" = "asc",
) => get<Track[]>(`/library/songs?sort=${sort}&order=${order}`, signal);

export const getArtistAlbums = (artist: string, signal?: AbortSignal) =>
  get<{ artist_name: string; albums: Album[] }>(
    `/library/artists/${encodeURIComponent(artist)}/albums`,
    signal,
  );

export const getAlbum = (albumRowid: number, signal?: AbortSignal) =>
  get<AlbumDetail>(`/library/albums/${albumRowid}/tracks`, signal);

export async function rateTrack(
  trackRowid: number,
  rating: Rating,
): Promise<Rating> {
  const response = await apiFetch("/api/ratings", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ track_rowid: trackRowid, rating }),
  });
  if (!response.ok) {
    throw new ApiError("/ratings", response.status, response.statusText);
  }
  return ((await response.json()) as { rating: Rating }).rating;
}

export type Playlist = {
  id: number;
  name: string;
  track_count: number;
  runtime_ms: number;
  cover_track_rowids: number[];
  created_at: number;
  updated_at: number;
};

export type PlaylistDetail = Playlist & { tracks: Track[] };

export const getPlaylists = (signal?: AbortSignal) =>
  get<Playlist[]>("/playlists", signal);

export const getPlaylist = (id: number, signal?: AbortSignal) =>
  get<PlaylistDetail>(`/playlists/${id}`, signal);

async function sendPlaylist(
  method: string,
  path: string,
  body?: unknown,
): Promise<Response> {
  const response = await apiFetch(`/api${path}`, {
    method,
    headers: { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    throw new ApiError(path, response.status, response.statusText);
  }
  return response;
}

export async function createPlaylist(
  name: string,
  trackRowids: number[],
): Promise<PlaylistDetail> {
  const response = await sendPlaylist("POST", "/playlists", {
    name,
    track_rowids: trackRowids,
  });
  return (await response.json()) as PlaylistDetail;
}

export async function renamePlaylist(
  id: number,
  name: string,
): Promise<PlaylistDetail> {
  const response = await sendPlaylist("PATCH", `/playlists/${id}`, { name });
  return (await response.json()) as PlaylistDetail;
}

export async function deletePlaylist(id: number): Promise<void> {
  await sendPlaylist("DELETE", `/playlists/${id}`);
}

/** Plain text, read-only. `null` means the track has no lyrics — which is an
 *  answer, not a failure, so it is not thrown. */
export async function getLyrics(
  trackRowid: number,
  signal?: AbortSignal,
): Promise<string | null> {
  const response = await apiFetch(`/api/tracks/${trackRowid}/lyrics`, { signal });
  if (response.status === 404) return null;
  if (!response.ok) {
    throw new ApiError(
      `/tracks/${trackRowid}/lyrics`,
      response.status,
      response.statusText,
    );
  }
  return response.text();
}

/** One timed line from a `.lrc` file, offset-free -- `offset_ms` from the
 *  parent response is applied client-side, once, on the way into the view. */
export type SyncedLyricsLine = { ms: number; text: string };

/** Exactly one of `lines`/`plain` is populated -- `synced` says which. Never
 *  both, and never neither: the endpoint 404s instead when a track has no
 *  lyrics at all. */
export type SyncedLyrics = {
  synced: boolean;
  lines: SyncedLyricsLine[] | null;
  plain: string | null;
  offset_ms: number;
  lrc_stale: boolean;
  timing_lines: string[];
};

export const getSyncedLyrics = (trackRowid: number, signal?: AbortSignal) =>
  get<SyncedLyrics>(`/tracks/${trackRowid}/lyrics/synced`, signal);

/** A rare, per-track correction (O11) -- not a setting, so there is nothing
 *  to read back beyond the value the server actually stored. */
export async function setLyricsOffset(
  trackRowid: number,
  offsetMs: number,
): Promise<number> {
  const response = await apiFetch(`/api/tracks/${trackRowid}/lyrics/offset`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ offset_ms: offsetMs }),
  });
  if (!response.ok) {
    throw new ApiError(
      `/tracks/${trackRowid}/lyrics/offset`,
      response.status,
      response.statusText,
    );
  }
  return ((await response.json()) as { offset_ms: number }).offset_ms;
}

export type AlignSkip = { track_rowid: number; reason: "no_lyrics" | "synced" | "missing_file" };

/** 503 = smart features missing, 409 = another align job running; tell them apart with `statusOf`. */
export const alignTracks = (trackRowids: number[], replace = false) =>
  send<{ job_id: number | null; skipped: AlignSkip[] }>("POST", "/tracks/align", {
    track_rowids: trackRowids,
    replace,
  });

export const saveTappedTimings = (trackRowid: number, startsMs: number[]) =>
  send<{ ok: boolean }>("PUT", `/tracks/${trackRowid}/lyrics/lrc`, { starts_ms: startsMs });

export const searchLibrary = (query: string, signal?: AbortSignal) =>
  get<{ q: string; results: Track[] }>(
    `/library/search?q=${encodeURIComponent(query)}`,
    signal,
  );

/* --- the dashboard ------------------------------------------------------- */

export type LibraryAlbum = {
  album_rowid: number;
  album_name: string;
  artist_name: string;
  track_count: number;
  runtime_ms: number;
  has_art: boolean;
};

export type RecommendedAlbum =
  | (LibraryAlbum & { owned: true; reason: string })
  | { owned: false; album_name: string; artist_name: string; reason: string };

export type Dashboard = {
  /** Songs the lyrics lookup found nothing for and nobody marked wordless. */
  missing_lyrics: number;
  generated_at: number;
  sounds_like: { seed: Track | null; tracks: Track[] };
  resume: { track: Track; position_ms: number; queue_length: number; queue_index: number } | null;
  /** Albums played most recently, newest first: "carry on with what I was
   *  doing" is the most likely intent on open. */
  recent_albums: LibraryAlbum[];
  never_played: LibraryAlbum[];
  forgotten: Track[];
  on_repeat: (Track & { plays: number })[];
  recently_added: LibraryAlbum[];
  for_fans_of: { artist_name: string; albums: LibraryAlbum[] } | null;
  recommended_albums: RecommendedAlbum[];
  stats: {
    library: {
      playable: number;
      missing: number;
      albums: number;
      artists: number;
      runtime_ms: number;
    };
    week: { day: string; weekday: string; minutes: number }[];
    listening: { plays: number; heard_ms: number };
    /** Null until there is enough history to rank honestly. */
    top_artists: { artist_name: string; plays: number; heard_ms: number }[] | null;
  };
};

export const getMissingLyrics = (signal?: AbortSignal) =>
  get<{ count: number; tracks: Track[] }>("/library/lyrics/missing", signal);

export const markNoWords = (trackRowid: number) =>
  post<{ ok: boolean }>(`/tracks/${trackRowid}/lyrics/no-words`);

export const getDashboard = (signal?: AbortSignal) =>
  get<Dashboard>("/dashboard", signal);

/* --- the recommender ----------------------------------------------------- */

export type Reasons = {
  relaxed: string[];
  arc: {
    target: number | null;
    darkness: number | null;
    theme_sim: number | null;
    filler: boolean;
  } | null;
  cost: number;
  [key: string]: unknown;
};

export type ArcPick = {
  track_rowid: number;
  score: number;
  reasons: Reasons;
  /** Hydrated server-side. The client needs whole tracks to render or play
   *  one, and a round trip per pick is not worth saving bytes on. */
  track: Track;
};

export type ArcBlock = {
  results: ArcPick[];
  reason: string | null;
  exhausted: boolean;
};

/** Radio picks from the arc engine. Stateless: `pos`, `recent` and `exclude`
 *  carry everything the server needs (story 35). */
export function getArcRadio(q: RadioQuery, signal?: AbortSignal): Promise<ArcBlock> {
  return get<ArcBlock>(radioPath(q), signal);
}

export function postArcRadio(body: {
  spec: object;
  n: number;
  pos: number;
  recent: number[];
  exclude: number[];
}): Promise<ArcBlock> {
  return send<ArcBlock>("POST", "/arc", radioBody(body));
}

export type VibeHit = { track_rowid: number; score: number; track: Track };

/** Free-text search over the CLAP audio space (story 45). Exploratory, not
 *  precise — PRD §504 measured text-to-audio retrieval as visibly noisier than
 *  audio-to-audio, and the UI says so. */
export function searchVibe(
  query: string,
  n = 30,
  signal?: AbortSignal,
): Promise<{ q: string; results: VibeHit[] }> {
  return get<{ q: string; results: VibeHit[] }>(
    `/library/search/vibe?q=${encodeURIComponent(query)}&n=${n}`,
    signal,
  );
}

export type ReleaseKind = "album" | "single";

/* --- MusicBrainz: the one browse source ------------------------------ */

/** A MusicBrainz artist from a name search. `score` rides through from
 *  MusicBrainz's own ranking, unrenormalized. `in_library` is how many tracks
 *  we hold under this name, credited only to the best-scored of same-named
 *  artists -- tracks carry no artist mbid to tell them apart. */
export type MbArtist = {
  mbid: string;
  name: string;
  disambiguation: string;
  score: number | null;
  in_library: number;
};

/** A release group -- every pressing of one record collapsed to one entry.
 *  `release_date` may be a full date, a truncated one ("2026", "2026-06"), or
 *  empty; whether that was enough to count as newer than the freeze is
 *  already decided server-side, so nothing here needs re-deciding it. */
export type MbReleaseGroup = {
  mbid: string;
  title: string;
  release_date: string;
  primary_type: string;
};

/** One track off the chosen pressing, self-sufficient: `release_mbid` and
 *  `album_name` are flattened onto every row, not only carried at the
 *  tracklist's top level, and each row carries its own artist credit -- so a
 *  client posts a row to `POST /api/jobs` (the `mb` job kind below) as it
 *  stands, with no merge. */
export type MbTrack = {
  recording_mbid: string;
  release_mbid: string;
  artist_name: string;
  artist_names: string[];
  album_name: string;
  track_name: string;
  track_no: number | null;
  disc_no: number;
  total_tracks: number | null;
  duration_ms: number | null;
  isrc: string | null;
  /** Computed server-side with the download's own identity rule, so the badge
   *  and the job agree on what counts as already held. A failed attempt leaves
   *  no finished file and must not read as owned. */
  in_library: boolean;
};

/** The chosen pressing's tracklist, plus the release-level facts each row
 *  above also carries its own copy of. */
export type MbTracklist = {
  release_mbid: string;
  album_name: string;
  release_date: string;
  artist_name: string;
  artist_names: string[];
  tracks: MbTrack[];
};

export const searchMbArtists = (query: string, signal?: AbortSignal) =>
  get<MbArtist[]>(`/discover/artists?q=${encodeURIComponent(query)}`, signal);

/** This artist's release groups of one kind, newest first. */
export const getMbReleases = (
  artistMbid: string,
  kind: ReleaseKind,
  signal?: AbortSignal,
) =>
  get<MbReleaseGroup[]>(
    `/discover/artists/${artistMbid}/releases?kind=${kind}`,
    signal,
  );

export const getMbTracks = (releaseGroupMbid: string, signal?: AbortSignal) =>
  get<MbTracklist>(
    `/discover/release-groups/${releaseGroupMbid}/tracks`,
    signal,
  );

/* --- queueing work -------------------------------------------------------- */

export type JobRequest =
  | {
      kind: "url";
      url: string;
      artist_name: string;
      track_name: string;
      album_name?: string;
    }
  | {
      kind: "mb";
      // These five are non-optional on purpose: they mirror
      // `MB_REQUIRED_FIELDS` in muse/api.py exactly, so a payload missing one
      // is a compile error here rather than a 400 from the server.
      // `recording_mbid` is what the MusicBrainz metadata tier keys on --
      // miss it and the download falls through to yt-dlp and quietly tags
      // whatever YouTube had. The other four are the columns `tracks`
      // declares NOT NULL.
      recording_mbid: string;
      artist_name: string;
      artist_names: string[];
      album_name: string;
      track_name: string;
      // Allowlisted on the server but not required -- legitimately absent
      // (MusicBrainz often has no ISRC, and a single has no track number
      // worth the name).
      release_mbid?: string;
      track_no?: number | null;
      disc_no?: number | null;
      total_tracks?: number | null;
      duration_ms?: number | null;
      isrc?: string | null;
    }
  | { kind: "mb_release"; release_group_mbid: string };

async function post<T>(path: string, body?: unknown): Promise<T> {
  const response = await apiFetch(`/api${path}`, {
    method: "POST",
    headers: body === undefined ? {} : { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    throw new ApiError(path, response.status, response.statusText);
  }
  return response.json() as Promise<T>;
}

/** A track the server declined to queue, and why.
 *
 *  Only the whole-record kind (`mb_release`) ever produces these: it builds
 *  its payloads from a MusicBrainz release the client never sees, and a hole
 *  in MusicBrainz's data -- a track it has no title for -- is not a reason to
 *  refuse the other eleven. The response says which ones did not go so the
 *  screen can, instead of quietly queueing fewer than it claimed.
 *
 *  `track_name` may be empty precisely because it is the field most likely to
 *  be the hole, so `missing` is what a sentence is built from, not the name. */
export type SkippedTrack = {
  recording_mbid: string | null;
  track_name: string;
  track_no: number | null;
  disc_no: number | null;
  missing: string[];
};

/** `skipped` comes back on every kind, empty where nothing was skipped, so
 *  no caller has to check whether the key is there before reading it. */
export const queueDownload = (request: JobRequest) =>
  post<{ job_ids: number[]; skipped: SkippedTrack[] }>("/jobs", request);

/** Retrying makes a new job rather than resetting the failed one, so the
 *  failure stays readable. The response says which job it is a retry of. */
export const retryJob = (jobId: number) =>
  post<{ job_ids: number[]; retry_of: number }>(`/jobs/${jobId}/retry`);

export type Job = {
  id: number;
  kind: string;
  payload: Record<string, unknown>;
  state: "queued" | "running" | "done" | "error" | "cancelled";
  progress: number;
  message: string | null;
  created_at: number;
  updated_at: number;
};

export const getJobs = (limit = 40, signal?: AbortSignal) =>
  get<Job[]>(`/jobs?limit=${limit}`, signal);

export type ChartSong = {
  rank: number;
  name: string;
  artist_name: string;
  url: string;
  track: Track | null;
};

export const getChart = (signal?: AbortSignal) =>
  get<{ songs: ChartSong[] }>("/charts/top", signal);

/* --- M2 editing ---------------------------------------------------------- */

export type DetailsValues = Partial<{
  track_name: string;
  artist_name: string;
  album_name: string;
  track_no: number;
  disc_no: number;
  year: number;
}>;

export async function saveDetails(
  trackRowids: number[],
  values: DetailsValues,
): Promise<Track[]> {
  const r = await send<{ tracks: Track[] }>("PATCH", "/tracks/details", {
    track_rowids: trackRowids,
    values,
  });
  return r.tracks;
}

export async function resetDetails(trackRowids: number[]): Promise<void> {
  await send("POST", "/tracks/details/reset", { track_rowids: trackRowids });
}

export type RemoveResult = {
  removed: number[];
  failed: { track_rowid: number; error: string }[];
};

export const removeTracks = (trackRowids: number[], trash: boolean) =>
  send<RemoveResult>("POST", "/tracks/remove", {
    track_rowids: trackRowids,
    trash,
  });

export async function restoreTracks(trackRowids: number[]): Promise<void> {
  await send("POST", "/tracks/restore", { track_rowids: trackRowids });
}

export const getRemovedTracks = (signal?: AbortSignal) =>
  get<Track[]>("/library/removed", signal);

export const getDuplicates = (signal?: AbortSignal) =>
  get<{ groups: Track[][] }>("/library/duplicates", signal);

export const getHiddenArtists = (signal?: AbortSignal) =>
  get<{ artist_name: string; hidden_at: number }[]>(
    "/library/hidden-artists",
    signal,
  );

export async function setArtistHidden(
  name: string,
  hidden: boolean,
): Promise<void> {
  await send("PUT", `/artists/${encodeURIComponent(name)}/hidden`, { hidden });
}

async function putFile(path: string, file: File): Promise<void> {
  const response = await apiFetch(`/api${path}`, {
    method: "PUT",
    headers: { "Content-Type": file.type },
    body: file,
  });
  if (!response.ok) throw await refusal(path, response);
}

export const uploadAlbumArt = (albumRowid: number, file: File) =>
  putFile(`/albums/${albumRowid}/art`, file);

export const uploadArtistImage = (name: string, file: File) =>
  putFile(`/artists/${encodeURIComponent(name)}/image`, file);

export const saveLyrics = (trackRowid: number, text: string) =>
  send<{ lrc_stale: boolean }>("PUT", `/tracks/${trackRowid}/lyrics`, { text });

export async function setLyricsTimings(
  trackRowid: number,
  action: "keep" | "drop",
): Promise<void> {
  await send("POST", `/tracks/${trackRowid}/lyrics/timings`, { action });
}

export const addToPlaylist = (playlistId: number, trackRowids: number[]) =>
  send<PlaylistDetail>("POST", `/playlists/${playlistId}/tracks`, {
    track_rowids: trackRowids,
  });
