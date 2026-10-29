import { useEffect, useState, type FormEvent } from "react";
import { getLyrics, saveLyrics, type Track } from "../api";
import { Dialog } from "./Dialog";

export function LyricsEditDialog({
  track,
  onClose,
  onSaved,
}: {
  track: Track;
  onClose: () => void;
  onSaved: (lrcStale: boolean) => void;
}) {
  const [text, setText] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const ac = new AbortController();
    getLyrics(track.track_rowid, ac.signal).then(
      (t) => setText(t ?? ""),
      (err) => {
        if (!ac.signal.aborted) setError((err as Error).message);
      },
    );
    return () => ac.abort();
  }, [track.track_rowid]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (text === null) return;
    setBusy(true);
    setError(null);
    try {
      const r = await saveLyrics(track.track_rowid, text);
      onSaved(r.lrc_stale);
      onClose();
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  return (
    <Dialog title="Edit lyrics" onClose={onClose}>
      <form className="dlg-form" onSubmit={submit}>
        <div className="field">
          <label htmlFor="ly-text">Lyrics</label>
          <textarea
            id="ly-text"
            rows={12}
            value={text ?? ""}
            disabled={text === null}
            onChange={(e) => setText(e.target.value)}
          />
        </div>
        <p className="dlg-help">Saving replaces the lyrics for this song only.</p>
        {error && <p className="error" role="alert">{error}</p>}
        <div className="dlg-actions">
          <button type="submit" className="btn primary" disabled={busy || text === null}>Save</button>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
        </div>
      </form>
    </Dialog>
  );
}
