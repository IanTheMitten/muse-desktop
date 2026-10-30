import { useEffect, useState, type CSSProperties, type FormEvent } from "react";

import {
  getDuplicates,
  getHiddenArtists,
  getRemovedTracks,
  getSettings,
  putMusicFolders,
  restoreTracks,
  getUpdate,
  lockDownloader,
  setArtistHidden,
  startScan,
  statusOf,
  unlockDownloader,
  type Settings as SettingsData,
  type Track,
  type UpdateInfo,
} from "../api";
import { Dialog } from "../components/Dialog";
import { RemoveDialog } from "../components/RemoveDialog";
import { count } from "../format";
import { settingsChanged } from "../useDownloader";
import { isUnlockChord } from "../unlock";
import { Screen } from "../components/Screen";
import { gb, useSmart } from "../components/SmartBanner";
import { useJobs } from "../jobs/JobsContext";
import { getCrossfadeSeconds, setCrossfadeSeconds } from "../player/crossfade";
import { shell } from "../shell";

export function Settings() {
  const { libraryVersion } = useJobs();
  const [data, setData] = useState<SettingsData | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Reload on a finished scan so the song counts catch up.
  useEffect(() => {
    const abort = new AbortController();
    getSettings(abort.signal)
      .then((next) => !abort.signal.aborted && setData(next))
      .catch((cause: unknown) => {
        if (!abort.signal.aborted) {
          setLoadError(cause instanceof Error ? cause.message : String(cause));
        }
      });
    return () => abort.abort();
  }, [libraryVersion]);

  const [asking, setAsking] = useState(false);
  const unlocked = data?.downloader_unlocked ?? true;
  useEffect(() => {
    if (unlocked) return;
    const onKey = (e: KeyboardEvent) => {
      if (!isUnlockChord(e)) return;
      e.preventDefault();
      setAsking(true);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [unlocked]);

  return (
    <Screen title="Settings" error={loadError} loading={!data}>
      {data && (
        <>
          <MusicFolders data={data} setData={setData} />
          <Playback />
          <SmartFeatures />
          <Duplicates />
          <HiddenArtists />
          <RemovedSongs />
          <Advanced data={data} setData={setData} />
          {asking && !unlocked && (
            <UnlockDialog onClose={() => setAsking(false)} setData={setData} />
          )}
        </>
      )}
    </Screen>
  );
}

/* Music folders as a mount table: path, live state, one action per row. */

function MusicFolders({
  data,
  setData,
}: {
  data: SettingsData;
  setData: (next: SettingsData) => void;
}) {
  const { job } = useJobs();
  const [path, setPath] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const save = async (folders: string[]) => {
    setBusy(true);
    setFormError(null);
    try {
      setData(await putMusicFolders(folders));
      return true;
    } catch (cause) {
      setFormError(cause instanceof Error ? cause.message : String(cause));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const folders = data?.music_folders ?? [];

  const add = async (event: FormEvent) => {
    event.preventDefault();
    const next = path.trim();
    if (!next) return;
    if (await save([...folders, next])) setPath("");
  };

  const choose = async () => {
    if (!shell) return;
    setFormError(null);
    try {
      const result = await shell.dialog.open({ directory: true, multiple: true });
      if (!result) return;
      await save([...folders, ...(Array.isArray(result) ? result : [result])]);
    } catch (cause) {
      setFormError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  const rescan = async () => {
    setFormError(null);
    try {
      await startScan();
    } catch (cause) {
      setFormError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  const scan = job?.kind === "scan" ? job : null;
  const scanning = scan?.state === "running" || scan?.state === "queued";
  const scanTotal = scan?.payload?.total;
  const status = !scan
    ? ""
    : scanning
      ? scanTotal
        ? `Scanning · ${scan.payload?.done ?? 0} of ${scanTotal}`
        : "Scanning"
      : (scan.message ?? "");

  return (
        <section className="section" aria-labelledby="folders-h">
          <div className="section-head">
            <h2 id="folders-h">Music folders</h2>
          </div>
          <p className="page-sub settings-help">
            Muse reads these folders and never changes the files in them.
          </p>

          {data.folder_status.length === 0 && !data.downloads ? (
            <p className="muted settings-empty">
              No folders yet. Add the folder your music lives in.
            </p>
          ) : (
            <ul className="folder-list">
              {data.downloads && (
                <li className="folder-row">
                  <div className="folder-info">
                    <span className="folder-path">Downloads (kept by Muse)</span>
                    <span className="folder-state">{count(data.downloads.songs, "song")}</span>
                  </div>
                </li>
              )}
              {data.folder_status.map((folder) => (
                <li key={folder.path} className="folder-row">
                  <div className="folder-info">
                    <span className="folder-path">{folder.path}</span>
                    <span className="folder-state">
                      {folder.present ? `${folder.songs} songs` : "Not connected"}
                    </span>
                  </div>
                  <button
                    type="button"
                    className="folder-remove"
                    disabled={busy}
                    aria-label={`Remove ${folder.path}`}
                    onClick={() => save(folders.filter((f) => f !== folder.path))}
                  >
                    Remove
                  </button>
                </li>
              ))}
            </ul>
          )}

          {shell ? (
            <div className="folder-add">
              <div className="folder-actions">
                <button type="button" className="btn" disabled={busy} onClick={choose}>
                  Choose folder…
                </button>
                <button type="button" className="btn" disabled={scanning} onClick={rescan}>
                  Rescan now
                </button>
              </div>
              <p className="folder-hint">You can also drag a folder onto this window.</p>
              {formError && (
                <p id="folder-error" className="error" role="alert">
                  {formError}
                </p>
              )}
            </div>
          ) : (
          <form className="folder-add" onSubmit={add}>
            <div className="field">
              <label htmlFor="folder-path">Folder path</label>
              <input
                id="folder-path"
                type="text"
                value={path}
                autoComplete="off"
                spellCheck={false}
                aria-invalid={formError ? true : undefined}
                aria-describedby={formError ? "folder-error" : undefined}
                onChange={(event) => setPath(event.target.value)}
              />
              {formError && (
                <p id="folder-error" className="error" role="alert">
                  {formError}
                </p>
              )}
            </div>
            <div className="folder-actions">
              <button type="submit" className="btn primary" disabled={busy || !path.trim()}>
                Add folder
              </button>
              <button type="button" className="btn" disabled={scanning} onClick={rescan}>
                Rescan now
              </button>
            </div>
          </form>
          )}

          <p className="folder-scan" role="status">
            {status}
          </p>
        </section>
  );
}

function Playback() {
  const [secs, setSecs] = useState(getCrossfadeSeconds);
  return (
    <section className="section" aria-labelledby="playback-h">
      <div className="section-head">
        <h2 id="playback-h">Playback</h2>
      </div>
      <div className="set-range">
        <label htmlFor="crossfade">Crossfade</label>
        <output htmlFor="crossfade">{secs === 0 ? "Off" : `${secs} s`}</output>
        <input
          id="crossfade"
          className="range"
          type="range"
          min={0}
          max={12}
          step={1}
          value={secs}
          style={{ "--p": `${(secs / 12) * 100}%` } as CSSProperties}
          onChange={(event) => {
            const n = Number(event.target.value);
            setSecs(n);
            setCrossfadeSeconds(n);
          }}
        />
      </div>
      <p className="page-sub settings-help">
        Songs blend into each other. Off plays them back to back with no gap.
      </p>
    </section>
  );
}

function SmartFeatures() {
  const { status, installing, failed, progressText, error, install } = useSmart();
  if (!status) return null;
  const size = gb(status.download_bytes);
  const line =
    status.state === "installed"
      ? "Installed"
      : status.state === "unsupported"
        ? "Not available on this computer. Smart features need a Mac with Apple silicon or Windows."
        : installing
          ? `Installing · ${progressText}`
          : `Not installed · ${size} download`;
  return (
    <section className="section" aria-labelledby="smart-h">
      <div className="section-head">
        <h2 id="smart-h">Smart features</h2>
      </div>
      <p className="page-sub settings-help">
        Muse listens to each song and reads its lyrics on this computer, so Radio and Muse pick
        better next songs. Nothing leaves your computer.
      </p>
      <ul className="folder-list">
        <li className="folder-row">
          <div className="folder-info">
            <span className="folder-path" role="status" aria-live="polite">{line}</span>
            {failed && <span className="folder-state">{error ?? status.job?.message}</span>}
          </div>
          {status.state === "not_installed" && !installing && (
            <button type="button" className="btn primary" onClick={install}>
              {failed ? "Try again" : "Install"}
            </button>
          )}
        </li>
      </ul>
      <p className="smart-licence">
        Uses MERT and an audio emotion model that are free for non-commercial use only, plus CLAP,
        MiniLM, an English emotion model, Laya, and lyric aligners (MMS-FA, free for
        non-commercial use only, and schufo).
      </p>
    </section>
  );
}

/* Loads a list on mount, on library change and on demand. */
function useList<T>(load: (signal: AbortSignal) => Promise<T>, enabled = true) {
  const { libraryVersion } = useJobs();
  const [items, setItems] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!enabled) return;
    const abort = new AbortController();
    load(abort.signal)
      .then((next) => {
        if (abort.signal.aborted) return;
        setItems(next);
        setError(null);
      })
      .catch((cause: unknown) => {
        if (!abort.signal.aborted) setError(cause instanceof Error ? cause.message : String(cause));
      });
    return () => abort.abort();
    // load is a stable api function
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [libraryVersion, tick, enabled]);
  return { items, error, setError, reload: () => setTick((t) => t + 1) };
}

const fmtTime = (ms: number | null) => {
  if (ms == null) return "?";
  const s = Math.round(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};

function Duplicates() {
  const [asked, setAsked] = useState(false);
  const { items, error, reload } = useList(getDuplicates, asked);
  const [keep, setKeep] = useState<Record<number, number>>({});
  const [removing, setRemoving] = useState<Track[] | null>(null);
  const groups = items?.groups ?? null;

  return (
    <section className="section" aria-labelledby="dups-h">
      <div className="section-head">
        <h2 id="dups-h">Duplicates</h2>
      </div>
      <div className="folder-actions set-gap">
        <button type="button" className="btn" onClick={() => (asked ? reload() : setAsked(true))}>
          Find duplicates
        </button>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      {groups && groups.length === 0 && <p className="muted settings-empty">No duplicates found.</p>}
      {groups?.map((group) => {
        const first = group.find((t) => t.playable) ?? group[0];
        const kept = keep[group[0].track_rowid] ?? first.track_rowid;
        return (
          <fieldset key={group[0].track_rowid} className="dup-group">
            <legend>
              {group[0].track_name} · {group[0].artist_name}
            </legend>
            {group.map((t) => (
              <label key={t.track_rowid} className="dup-copy">
                <input
                  type="radio"
                  name={`dup-${group[0].track_rowid}`}
                  checked={kept === t.track_rowid}
                  onChange={() => setKeep({ ...keep, [group[0].track_rowid]: t.track_rowid })}
                />
                <span className="folder-info">
                  <span className="folder-path">
                    {t.album_name} · {t.format ? t.format.toUpperCase() : "?"} · {fmtTime(t.duration_ms)}
                  </span>
                  <span className="folder-state">Keep</span>
                </span>
              </label>
            ))}
            <button
              type="button"
              className="btn dup-remove"
              onClick={() => setRemoving(group.filter((t) => t.track_rowid !== kept))}
            >
              Remove the other copies
            </button>
          </fieldset>
        );
      })}
      {removing && (
        <RemoveDialog tracks={removing} onClose={() => setRemoving(null)} onRemoved={reload} />
      )}
    </section>
  );
}

function HiddenArtists() {
  const { items, error, setError, reload } = useList(getHiddenArtists);
  return (
    <section className="section" aria-labelledby="hidden-h">
      <div className="section-head">
        <h2 id="hidden-h">Hidden artists</h2>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      {items && items.length === 0 && <p className="muted settings-empty">No hidden artists.</p>}
      {items && items.length > 0 && (
        <ul className="folder-list">
          {items.map((a) => (
            <li key={a.artist_name} className="folder-row">
              <div className="folder-info">
                <span className="folder-path">{a.artist_name}</span>
              </div>
              <button
                type="button"
                className="folder-remove"
                aria-label={`Unhide ${a.artist_name}`}
                onClick={() =>
                  setArtistHidden(a.artist_name, false).then(reload, (c: unknown) =>
                    setError(c instanceof Error ? c.message : String(c)),
                  )
                }
              >
                Unhide
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function RemovedSongs() {
  const { items, error, setError, reload } = useList(getRemovedTracks);
  return (
    <section className="section" aria-labelledby="removed-h">
      <div className="section-head">
        <h2 id="removed-h">Removed songs</h2>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      {items && items.length === 0 && <p className="muted settings-empty">Nothing removed.</p>}
      {items && items.length > 0 && (
        <ul className="folder-list">
          {items.map((t) => (
            <li key={t.track_rowid} className="folder-row">
              <div className="folder-info">
                <span className="folder-path">{t.track_name}</span>
                <span className="folder-state">
                  {t.artist_name} · {t.album_name}
                </span>
              </div>
              <button
                type="button"
                className="folder-remove"
                aria-label={`Restore ${t.track_name}`}
                onClick={() =>
                  restoreTracks([t.track_rowid]).then(reload, (c: unknown) =>
                    setError(c instanceof Error ? c.message : String(c)),
                  )
                }
              >
                Restore
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Advanced({
  data,
  setData,
}: {
  data: SettingsData;
  setData: (next: SettingsData) => void;
}) {
  const [update, setUpdate] = useState<UpdateInfo | null>(null);
  useEffect(() => {
    const abort = new AbortController();
    getUpdate(abort.signal).then(
      (next) => !abort.signal.aborted && setUpdate(next),
      () => {},
    );
    return () => abort.abort();
  }, []);

  const unlocked = data.downloader_unlocked;
  const lock = async () => {
    setData(await lockDownloader());
    settingsChanged();
  };

  return (
    <section className="section" aria-labelledby="advanced-h">
      <div className="section-head">
        <h2 id="advanced-h">Advanced</h2>
      </div>
      <ul className="folder-list">
        {update && (
          <li className="folder-row">
            <div className="folder-info">
              <span className="folder-path">Muse {update.current}</span>
              {update.latest != null && (
                <a
                  className="folder-state set-link"
                  href={update.url}
                  target="_blank"
                  rel="noreferrer"
                  onClick={(event) => {
                    if (!shell || !update.url) return;
                    event.preventDefault();
                    void shell.shell.open(update.url);
                  }}
                >
                  Muse {update.latest} is out
                </a>
              )}
            </div>
          </li>
        )}
        <li className="folder-row">
          <div className="folder-info">
            <span className="folder-path">External downloader</span>
            <span className="folder-state">
              {unlocked ? "On. Add is in the menu." : "Plugins are under development."}
            </span>
          </div>
          {unlocked && (
            <button type="button" className="folder-remove" onClick={lock}>
              Lock again
            </button>
          )}
        </li>
      </ul>
    </section>
  );
}

function UnlockDialog({
  onClose,
  setData,
}: {
  onClose: () => void;
  setData: (next: SettingsData) => void;
}) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setData(await unlockDownloader(password));
      settingsChanged();
      setPassword("");
      onClose();
    } catch (cause) {
      setError(statusOf(cause) === 403 ? "That's not it." : "Couldn't unlock. Try again.");
      setBusy(false);
    }
  };

  return (
    <Dialog title="Administrator password" onClose={onClose}>
      <form className="dlg-form" onSubmit={submit}>
        <div className="field">
          <label htmlFor="admin-password">Password</label>
          <input
            id="admin-password"
            type="password"
            autoComplete="off"
            autoFocus
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </div>
        {error && <p className="error" role="alert">{error}</p>}
        <div className="dlg-actions">
          <button type="submit" className="btn primary" disabled={busy}>Unlock</button>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
        </div>
      </form>
    </Dialog>
  );
}
