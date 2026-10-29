import type { ReactNode } from "react";

import { CoverArt } from "./CoverArt";

const fmt = (ms: number) => {
  const s = Math.max(0, Math.round(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};

export function TrackRow(props: {
  n?: number;
  title: string;
  sub?: string;
  durationMs: number | null;
  artSrc?: string | null;
  current?: boolean;
  paused?: boolean;
  unplayable: string | null;
  onPlay: () => void;
  variant?: "plain" | "song" | "queue" | "chart";
  leading?: ReactNode;
  trailing?: ReactNode;
}) {
  const { n, title, sub, durationMs, artSrc, current, paused, unplayable, onPlay, variant = "plain", leading, trailing } = props;
  const showArt = artSrc !== undefined && variant !== "plain";
  const playable = unplayable === null;
  const label = playable ? sub : unplayable;
  return (
    <li className={paused ? "paused" : undefined}>
      {leading}
      <button
        type="button"
        className={`row-btn row-${variant}`}
        aria-current={current ? "true" : undefined}
        aria-disabled={playable ? undefined : "true"}
        onClick={playable ? onPlay : undefined}
      >
        {n !== undefined && (
          <span className="row-no">
            {current ? (
              <span className="eq" aria-hidden="true"><i /><i /><i /></span>
            ) : (
              n
            )}
          </span>
        )}
        {showArt && (
          <span className="row-art">
            <CoverArt src={artSrc ?? null} hasArt={Boolean(artSrc)} size="thumb" />
            {current && n === undefined && (
              <span className="eq eq-over" aria-hidden="true"><i /><i /><i /></span>
            )}
          </span>
        )}
        <span className="row-name">
          {title}
          {label && <span className="row-sub">{label}</span>}
        </span>
        <span className="row-dur">{durationMs !== null ? fmt(durationMs) : ""}</span>
      </button>
      {trailing}
    </li>
  );
}
