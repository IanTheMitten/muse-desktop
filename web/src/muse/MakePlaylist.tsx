import { useRef, useState } from "react";
import { Link } from "react-router-dom";

import { createPlaylist } from "../api";
import type { Feel } from "./FeelPicker";
import { postArcPlaylist, toArcSpec } from "./mood";
import "../styles/muse.css";

const LENGTHS = [12, 20, 30];

/* Name, length and Create for a playlist built from a Feel. The plan is kept
 * (keyed by the mood and length it was planned for) so a failed save retries
 * without replanning, and a changed mood always replans. */
export function MakePlaylist({
  feel,
  seed,
  onSaved,
}: {
  feel: Feel;
  seed: number | null;
  onSaved?: () => void;
}) {
  const { dot, preset } = feel;
  const [length, setLength] = useState(12);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState("");
  const [saved, setSaved] = useState<{ id: number } | null>(null);
  const pending = useRef<{ key: string; ids: number[] } | null>(null);

  const create = async () => {
    setBusy(true);
    setToast("");
    setSaved(null);
    const key = `${dot.v}|${dot.a}|${length}|${seed}`;
    try {
      if (pending.current?.key !== key) {
        pending.current = { key, ids: await postArcPlaylist(toArcSpec(dot, seed, length), length) };
      }
    } catch {
      setBusy(false);
      setToast("Could not plan the playlist. Try again.");
      return;
    }
    const ids = pending.current.ids;
    const finalName = name.trim() || `${preset ?? "My"} mix`;
    try {
      const made = await createPlaylist(finalName, ids);
      setSaved({ id: made.id });
      setToast(`Saved "${finalName}" with ${ids.length} tracks.`);
      pending.current = null;
      onSaved?.();
    } catch {
      setToast("Could not save. Try again.");
    }
    setBusy(false);
  };

  return (
    <>
      <div className="make">
        <div className="field">
          <label htmlFor="plName">Name</label>
          <input id="plName" value={name} onChange={(e) => setName(e.target.value)} autoComplete="off" />
        </div>
        <div className="seg" role="group" aria-label="Length">
          {LENGTHS.map((n) => (
            <button key={n} type="button" aria-pressed={length === n} onClick={() => setLength(n)}>{n}</button>
          ))}
        </div>
        <button type="button" className="btn primary" disabled={busy} aria-busy={busy} onClick={create}>
          Create playlist
        </button>
      </div>
      <p className="toast" role="status">
        {toast}
        {saved && <> <Link to={`/playlists/${saved.id}`}>Open it</Link></>}
      </p>
    </>
  );
}
