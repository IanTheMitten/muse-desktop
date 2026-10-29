import { useState, type FormEvent } from "react";
import { resetDetails, saveDetails, type DetailsValues, type Track } from "../api";
import { Dialog } from "./Dialog";

type Key = keyof DetailsValues;

const FIELDS: { key: Key; label: string; numeric: boolean; read: (t: Track) => string | number | null }[] = [
  { key: "track_name", label: "Title", numeric: false, read: (t) => t.track_name },
  { key: "artist_name", label: "Artist", numeric: false, read: (t) => t.artist_name },
  { key: "album_name", label: "Album", numeric: false, read: (t) => t.album_name },
  { key: "track_no", label: "Track number", numeric: true, read: (t) => t.track_no },
  { key: "disc_no", label: "Disc number", numeric: true, read: (t) => t.disc_no },
  { key: "year", label: "Year", numeric: true, read: (t) => t.year },
];

function shared(tracks: Track[], read: (t: Track) => string | number | null): string {
  const first = read(tracks[0]);
  if (first == null || !tracks.every((t) => read(t) === first)) return "";
  return String(first);
}

export function EditDetailsDialog({
  tracks,
  onClose,
  onSaved,
}: {
  tracks: Track[];
  onClose: () => void;
  onSaved: (tracks: Track[]) => void;
}) {
  const many = tracks.length > 1;
  const fields = many ? FIELDS.filter((f) => f.key !== "track_name") : FIELDS;
  const prefill = Object.fromEntries(fields.map((f) => [f.key, shared(tracks, f.read)]));
  const [values, setValues] = useState<Record<string, string>>(prefill);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ids = tracks.map((t) => t.track_rowid);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const changed: Record<string, string | number> = {};
    for (const f of fields) {
      const v = values[f.key].trim();
      if (v !== "" && v !== prefill[f.key]) changed[f.key] = f.numeric ? Number(v) : v;
    }
    if (Object.keys(changed).length === 0) return onClose();
    setBusy(true);
    setError(null);
    try {
      onSaved(await saveDetails(ids, changed as DetailsValues));
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  async function reset() {
    setBusy(true);
    setError(null);
    try {
      await resetDetails(ids);
      onSaved(tracks);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  return (
    <Dialog title={many ? `Edit ${tracks.length} songs` : "Edit details"} onClose={onClose}>
      <form className="dlg-form" onSubmit={submit}>
        {many && <p className="dlg-help">Leave a field empty to keep each song's own value.</p>}
        {fields.map((f) => (
          <div className="field" key={f.key}>
            <label htmlFor={`ed-${f.key}`}>{f.label}</label>
            <input
              id={`ed-${f.key}`}
              type={f.numeric ? "number" : "text"}
              min={f.numeric ? 0 : undefined}
              step={f.numeric ? 1 : undefined}
              value={values[f.key]}
              onChange={(e) => setValues({ ...values, [f.key]: e.target.value })}
            />
          </div>
        ))}
        {error && <p className="error" role="alert">{error}</p>}
        <div className="dlg-actions">
          <button type="submit" className="btn primary" disabled={busy}>Save</button>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="button" className="btn" disabled={busy} onClick={reset}>Reset to file tags</button>
        </div>
        <p className="dlg-help">Goes back to the tags in the file after the next scan.</p>
      </form>
    </Dialog>
  );
}
