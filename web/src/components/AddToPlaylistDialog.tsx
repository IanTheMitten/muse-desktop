import { useEffect, useState, type FormEvent } from "react";
import { addToPlaylist, createPlaylist, getPlaylists, type Playlist, type Track } from "../api";
import { Dialog } from "./Dialog";

const CREATE_CAP = 100;

export function AddToPlaylistDialog({
  tracks,
  onClose,
  onAdded,
}: {
  tracks: Track[];
  onClose: () => void;
  onAdded: () => void;
}) {
  const [playlists, setPlaylists] = useState<Playlist[] | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ids = tracks.map((t) => t.track_rowid);

  useEffect(() => {
    const ac = new AbortController();
    getPlaylists(ac.signal).then(setPlaylists, (err) => {
      if (!ac.signal.aborted) setError((err as Error).message);
    });
    return () => ac.abort();
  }, []);

  async function run(job: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await job();
      onAdded();
      onClose();
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  function create(e: FormEvent) {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return;
    void run(async () => {
      const made = await createPlaylist(trimmed, ids.slice(0, CREATE_CAP));
      if (ids.length > CREATE_CAP) await addToPlaylist(made.id, ids.slice(CREATE_CAP));
    });
  }

  return (
    <Dialog title="Add to playlist" onClose={onClose}>
      <div className="dlg-form">
        {playlists && playlists.length > 0 && (
          <ul className="dlg-list">
            {playlists.map((p) => (
              <li key={p.id}>
                <button type="button" disabled={busy} onClick={() => run(() => addToPlaylist(p.id, ids))}>
                  {p.name} · {p.track_count} songs
                </button>
              </li>
            ))}
          </ul>
        )}
        <form className="dlg-form" onSubmit={create}>
          <div className="field">
            <label htmlFor="pl-new">New playlist</label>
            <input id="pl-new" type="text" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          {error && <p className="error" role="alert">{error}</p>}
          <div className="dlg-actions">
            <button type="submit" className="btn primary" disabled={busy || !name.trim()}>Create</button>
            <button type="button" className="btn" onClick={onClose}>Cancel</button>
          </div>
        </form>
      </div>
    </Dialog>
  );
}
