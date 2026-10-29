import { useEffect, useState } from "react";

import { useJobs } from "../jobs/JobsContext";

/* The one place background work is visible.
 *
 * Deliberately a strip and not a modal: fetching covers is something the app
 * does *while* being used, and anything that had to be dismissed would be
 * lying about how important it is. */

const LINGER_MS = 6000;

export function JobProgress() {
  const { job } = useJobs();
  const [showFinished, setShowFinished] = useState(false);

  const running = job?.state === "running" || job?.state === "queued";
  const finished = job?.state === "done" || job?.state === "error";

  // A run that ends silently leaves no answer to "did that work?", so the
  // result stays up for a few seconds and then gets out of the way.
  useEffect(() => {
    if (!finished) return;
    setShowFinished(true);
    const timer = window.setTimeout(() => setShowFinished(false), LINGER_MS);
    return () => window.clearTimeout(timer);
  }, [finished, job?.id]);

  if (!job || (!running && !showFinished)) return null;

  const done = job.payload?.done ?? 0;
  const art = job.payload?.albums;
  const scan = job.payload?.total;
  let label: string;
  if (!running) label = job.message ?? "Finished";
  else if (job.kind === "scan") label = scan ? `Scanning · ${done} of ${scan}` : "Scanning";
  else if (job.kind === "lyrics_sync") label = "Fetching lyrics";
  else if (job.kind === "art_backfill") {
    label = art ? `Fetching cover art · ${done} of ${art}` : "Fetching cover art";
  } else label = job.message ?? "Working";

  return (
    <div className="job-strip" role="status" aria-live="polite">
      <div className="job-strip-body">
        <span className="job-strip-label">{label}</span>
        {running && (
          <span className="job-strip-count">
            {Math.round(job.progress * 100)}%
          </span>
        )}
      </div>
      <div
        className="job-strip-track"
        role="progressbar"
        aria-valuenow={Math.round(job.progress * 100)}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={label}
      >
        <div
          className="job-strip-fill"
          style={{ transform: `scaleX(${running ? job.progress : 1})` }}
        />
      </div>
    </div>
  );
}
