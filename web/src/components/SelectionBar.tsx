import { CloseIcon } from "./Icons";
import type { TrackAction } from "./TrackMenu";
import "../styles/actions.css";

const ACTIONS: { action: TrackAction; label: string }[] = [
  { action: "next", label: "Play next" },
  { action: "queue", label: "Add to queue" },
  { action: "playlist", label: "Add to playlist" },
  { action: "details", label: "Edit details" },
  { action: "align", label: "Auto-align" },
  { action: "remove", label: "Remove" },
];

export function SelectionBar({
  count,
  onAction,
  onClear,
}: {
  count: number;
  onAction: (action: TrackAction) => void;
  onClear: () => void;
}) {
  return (
    <div className="sel-bar" role="region" aria-label="Selection">
      <p className="sel-count" role="status">{count} selected</p>
      <div className="sel-actions">
        {ACTIONS.map(({ action, label }) => (
          <button key={action} type="button" className={`sel-act${action === "next" ? " primary" : ""}`} onClick={() => onAction(action)}>
            {label}
          </button>
        ))}
      </div>
      <button type="button" className="icon-btn sel-clear" aria-label="Clear selection" onClick={onClear}>
        <CloseIcon />
      </button>
    </div>
  );
}
