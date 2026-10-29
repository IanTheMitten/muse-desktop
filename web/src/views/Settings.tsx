import { useEffect, useState, type FormEvent } from "react";

import { getSettings, putMusicFolders, startScan, type Settings as SettingsData } from "../api";
import { Screen } from "../components/Screen";
import { useJobs } from "../jobs/JobsContext";

/* Music folders as a mount table: path, live state, one action per row. */

export function Settings() {
  const { job, libraryVersion } = useJobs();
  const [data, setData] = useState<SettingsData | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [path, setPath] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

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
    <Screen title="Settings" error={loadError} loading={!data}>
      {data && (
        <section className="section" aria-labelledby="folders-h">
          <div className="section-head">
            <h2 id="folders-h">Music folders</h2>
          </div>
          <p className="page-sub settings-help">
            Muse reads these folders and never changes the files in them.
          </p>

          {data.folder_status.length === 0 ? (
            <p className="muted settings-empty">
              No folders yet. Add the folder your music lives in.
            </p>
          ) : (
            <ul className="folder-list">
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

          <p className="folder-scan" role="status">
            {status}
          </p>
        </section>
      )}
    </Screen>
  );
}
