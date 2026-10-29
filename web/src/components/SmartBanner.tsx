import { useCallback, useEffect, useState } from "react";
import { useLocation } from "react-router-dom";

import { getSmartStatus, installSmartFeatures, type SmartStatus } from "../api";
import { useJobs } from "../jobs/JobsContext";
import "../styles/smart.css";

/* The offer and its progress are one object: a single glass line whose accent
 * fill grows behind the text while the install runs. Never modal. */

let dismissed = false; // "Not now" lasts until the next launch, so no storage

export const gb = (bytes: number) => `${(bytes / 1e9).toFixed(1)} GB`;

export type SmartView = {
  status: SmartStatus | null;
  /** The install job, live from the stream when it is the one running. */
  job: { state: string; progress: number; message: string | null } | null;
  installing: boolean;
  failed: boolean;
  progressText: string;
  error: string | null;
  install: () => void;
};

export function useSmart(): SmartView {
  const { job: streamed } = useJobs();
  const [status, setStatus] = useState<SmartStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const live = streamed?.kind === "smart_install" ? streamed : null;
  const liveKey = live ? `${live.id}:${live.state}` : "";

  useEffect(() => {
    const abort = new AbortController();
    getSmartStatus(abort.signal)
      .then((next) => !abort.signal.aborted && setStatus(next))
      .catch(() => {}); // no status, no banner: the app works without it
    return () => abort.abort();
  }, [liveKey]);

  const job = live ?? status?.job ?? null;
  const installing = job?.state === "running" || job?.state === "queued";
  const failed = !installing && job?.state === "error" && status?.state === "not_installed";
  const total = status?.download_bytes ?? 0;
  const progressText = `${gb((job?.progress ?? 0) * total)} of ${gb(total)}`;

  const install = useCallback(() => {
    setError(null);
    installSmartFeatures()
      .then(() => getSmartStatus())
      .then(setStatus)
      .catch((cause: unknown) => setError(cause instanceof Error ? cause.message : String(cause)));
  }, []);

  return { status, job, installing, failed, progressText, error, install };
}

export function SmartBanner() {
  const { pathname } = useLocation();
  const smart = useSmart();
  const [hidden, setHidden] = useState(dismissed);
  const { status, job, installing, failed, error, install } = smart;

  if (!status || status.state !== "not_installed" || pathname === "/settings") return null;
  if (hidden && !installing && !failed) return null;

  const pct = Math.round((job?.progress ?? 0) * 100);
  const size = gb(status.download_bytes);

  return (
    <div className="smart-banner" role="status" aria-live="polite">
      {installing && (
        <div className="smart-fill" style={{ transform: `scaleX(${job?.progress ?? 0})` }} />
      )}
      <p className="smart-text">
        {installing
          ? `Installing smart features · ${smart.progressText}`
          : failed
            ? `Smart features didn't install. ${error ?? job?.message ?? ""}`
            : `Smart features aren't installed. Muse can listen to your songs and read their lyrics to choose better next songs. ${size} download.`}
      </p>
      {installing ? (
        <span className="smart-count">{pct}%</span>
      ) : (
        <div className="smart-actions">
          <button type="button" className="btn primary" onClick={install}>
            {failed ? "Try again" : "Install"}
          </button>
          {!failed && (
            <button
              type="button"
              className="btn"
              onClick={() => {
                dismissed = true;
                setHidden(true);
              }}
            >
              Not now
            </button>
          )}
        </div>
      )}
    </div>
  );
}
