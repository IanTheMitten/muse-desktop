import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import type { Track } from "../api";
import { MoreIcon } from "./Icons";
import "../styles/actions.css";

export type TrackAction = "next" | "queue" | "playlist" | "details" | "lyrics" | "align" | "remove";

const ITEMS: { action: TrackAction; label: string }[] = [
  { action: "next", label: "Play next" },
  { action: "queue", label: "Add to queue" },
  { action: "playlist", label: "Add to playlist…" },
  { action: "details", label: "Edit details" },
  { action: "lyrics", label: "Edit lyrics" },
  { action: "align", label: "Auto-align lyrics" },
  { action: "remove", label: "Remove from library…" },
];

export function TrackMenu({ track, onAction }: { track: Track; onAction: (action: TrackAction) => void }) {
  const [open, setOpen] = useState(false);
  const [up, setUp] = useState(false);
  const wrap = useRef<HTMLSpanElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    menu.current?.querySelector<HTMLElement>("[role=menuitem]")?.focus();
    const away = (e: PointerEvent) => {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", away);
    return () => document.removeEventListener("pointerdown", away);
  }, [open]);

  function toggle() {
    if (!open) {
      const rect = button.current!.getBoundingClientRect();
      setUp(window.innerHeight - rect.bottom < 320);
    }
    setOpen(!open);
  }

  function onKeyDown(e: KeyboardEvent) {
    if (!open) return;
    const items = [...menu.current!.querySelectorAll<HTMLElement>("[role=menuitem]")];
    const i = items.indexOf(document.activeElement as HTMLElement);
    if (e.key === "Escape") {
      e.stopPropagation();
      setOpen(false);
      button.current?.focus();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      items[(i + 1) % items.length].focus();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      items[(i - 1 + items.length) % items.length].focus();
    } else if (e.key === "Home") {
      e.preventDefault();
      items[0].focus();
    } else if (e.key === "End") {
      e.preventDefault();
      items[items.length - 1].focus();
    } else if (e.key === "Tab") {
      setOpen(false);
    }
  }

  return (
    <span className="more" ref={wrap} onKeyDown={onKeyDown}>
      <button
        type="button"
        ref={button}
        className="icon-btn more-btn"
        aria-label={`More for ${track.track_name}`}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={toggle}
      >
        <MoreIcon />
      </button>
      {open && (
        <div className={`more-menu${up ? " up" : ""}`} role="menu" aria-label={`Actions for ${track.track_name}`} ref={menu}>
          {ITEMS.filter((i) => i.action !== "align" || track.has_lyrics).map(({ action, label }) => (
            <button
              key={action}
              type="button"
              role="menuitem"
              tabIndex={-1}
              onClick={() => {
                setOpen(false);
                onAction(action);
              }}
            >
              {label}
            </button>
          ))}
        </div>
      )}
    </span>
  );
}
