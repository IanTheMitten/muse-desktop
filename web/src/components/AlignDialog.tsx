import { useEffect, useRef, useState } from "react";
import { alignTracks, statusOf, type Track } from "../api";
import { Dialog } from "./Dialog";

type View = "pending" | "confirm" | { error: string };

export function AlignDialog({ tracks, onClose }: { tracks: Track[]; onClose: () => void }) {
  const [view, setView] = useState<View>("pending");
  const started = useRef(false);
  const ids = tracks.map((t) => t.track_rowid);

  async function post(replace: boolean) {
    try {
      const r = await alignTracks(ids, replace);
      if (!replace && ids.length === 1 && r.skipped[0]?.reason === "synced") setView("confirm");
      else if (r.job_id === null && r.skipped.length > 0 && r.skipped.every((s) => s.reason === "no_lyrics")) {
        setView({ error: "Nothing to align: these songs have no lyrics." });
      } else onClose();
    } catch (cause) {
      const status = statusOf(cause);
      if (status === 503) setView({ error: "Auto-align needs smart features. Install them in Settings." });
      else if (status === 409) setView({ error: "Already aligning other songs. Try again when that finishes." });
      else setView({ error: (cause as Error).message });
    }
  }

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    void post(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (view === "pending") return null;
  if (view === "confirm") {
    return (
      <Dialog title="Replace the current timings?" onClose={onClose}>
        <div className="dlg-form">
          <p>These lyrics already have timings. Auto-align makes new ones from the song.</p>
          <div className="dlg-actions">
            <button type="button" className="btn primary" onClick={() => { setView("pending"); void post(true); }}>Replace</button>
            <button type="button" className="btn" onClick={onClose}>Keep</button>
          </div>
        </div>
      </Dialog>
    );
  }
  return (
    <Dialog title="Auto-align" onClose={onClose}>
      <div className="dlg-form">
        <p role="alert">{view.error}</p>
        <div className="dlg-actions">
          <button type="button" className="btn primary" onClick={onClose}>Close</button>
        </div>
      </div>
    </Dialog>
  );
}
