import { useState } from "react";
import { removeTracks, type Track } from "../api";
import { Dialog } from "./Dialog";

export function RemoveDialog({
  tracks,
  onClose,
  onRemoved,
}: {
  tracks: Track[];
  onClose: () => void;
  onRemoved: (removed: number[]) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const ids = tracks.map((t) => t.track_rowid);

  async function run(trash: boolean) {
    setBusy(true);
    setFailure(null);
    try {
      const r = await removeTracks(ids, trash);
      onRemoved(r.removed);
      if (r.failed.length === 0) return onClose();
      setFailure(`Couldn't move ${r.failed.length} to Trash: ${r.failed[0].error}`);
    } catch (err) {
      setFailure((err as Error).message);
      setBusy(false);
    }
  }

  const title = tracks.length === 1 ? `Remove "${tracks[0].track_name}"?` : `Remove ${tracks.length} songs?`;
  return (
    <Dialog title={title} onClose={onClose}>
      <div className="dlg-form">
        <p>Muse stops showing them and keeps their play history. You can restore them in Settings.</p>
        {failure && <p className="error" role="alert">{failure}</p>}
        {failure ? (
          <div className="dlg-actions">
            <button type="button" className="btn primary" onClick={onClose}>Close</button>
          </div>
        ) : (
          <>
            <div className="dlg-actions">
              <button type="button" className="btn primary" disabled={busy} onClick={() => run(false)}>Remove from library</button>
              <button type="button" className="btn" disabled={busy} onClick={() => run(true)}>Also move to Trash</button>
              <button type="button" className="btn" onClick={onClose}>Cancel</button>
            </div>
            <p className="dlg-help">Move to Trash also deletes the files from your music folder.</p>
          </>
        )}
      </div>
    </Dialog>
  );
}
