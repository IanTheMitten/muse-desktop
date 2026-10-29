import { useEffect, useState } from "react";

import { useJobs } from "../jobs/JobsContext";

/* Cover art, and the thing that stands in for it.
 *
 * A missing cover is ordinary — 87 albums, and the Cover Art Archive does not
 * have all of them — so the absence is designed rather than handled. There is
 * no broken-image state and no error the caller has to special-case: either
 * there is a cover, or there are grooves. */

type Size = "thumb" | "bar" | "hero" | "label" | "shelf" | "tile";

export function CoverArt({
  src,
  hasArt,
  size,
  alt = "",
}: {
  /** The endpoint for this album's or track's cover, or null when unknown. */
  src: string | null;
  /** What the library believes. A hint, not a guarantee — the file can be
   *  deleted, and the backfill can fill it in after this view loaded. */
  hasArt: boolean;
  size: Size;
  alt?: string;
}) {
  const { artVersion } = useJobs();
  const [broken, setBroken] = useState(false);

  // A new track, or art that has just been fetched, deserves another attempt.
  useEffect(() => setBroken(false), [src, artVersion]);

  // Before any backfill has run, `has_art` is false everywhere and asking
  // would be 87 pointless 404s. After one has finished, the data this view
  // loaded from is stale, so it is worth asking anyway.
  const worthTrying = Boolean(src) && (hasArt || artVersion > 0) && !broken;

  return (
    <span className={`art art-${size}`}>
      {worthTrying ? (
        <img
          className="art-image"
          src={artVersion > 0 ? `${src}?v=${artVersion}` : (src as string)}
          alt={alt}
          loading="lazy"
          decoding="async"
          draggable={false}
          onError={() => setBroken(true)}
        />
      ) : (
        <span className="art-tile" />
      )}
    </span>
  );
}
