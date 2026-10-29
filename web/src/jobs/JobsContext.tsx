import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

/* Background work, as the UI sees it.
 *
 * One EventSource for the whole app rather than one per view: the browser caps
 * connections per origin, and a stream opened by a component that unmounts on
 * navigation would drop progress every time the listener taps an album. */

export type Job = {
  id: number;
  kind: string | null;
  state: "queued" | "running" | "done" | "error" | "cancelled" | "idle";
  progress: number;
  message: string | null;
  payload?: { albums?: number; done?: number };
};

type JobsApi = {
  job: Job | null;
  /** Bumped when a backfill finishes, so views that loaded before the art
   *  existed can start asking for it without a page reload. */
  artVersion: number;
};

const JobsContext = createContext<JobsApi>({ job: null, artVersion: 0 });

export const useJobs = () => useContext(JobsContext);

export function JobsProvider({ children }: { children: ReactNode }) {
  const [job, setJob] = useState<Job | null>(null);
  const [artVersion, setArtVersion] = useState(0);
  const wasRunning = useRef(false);

  useEffect(() => {
    const source = new EventSource("/api/jobs/stream");

    source.onmessage = (event) => {
      let next: Job;
      try {
        next = JSON.parse(event.data) as Job;
      } catch {
        return;
      }
      setJob(next);

      // A run that has just ended is the only moment art appears where there
      // was none. Bumping here is what makes the covers show up on the screen
      // the listener is already looking at.
      const running = next.state === "running" || next.state === "queued";
      if (wasRunning.current && !running) {
        setArtVersion((version) => version + 1);
      }
      wasRunning.current = running;
    };

    /* EventSource reconnects on its own. Nothing here needs to know: job state
     * is re-sent to every new subscriber the moment it connects. */
    source.onerror = () => {};

    return () => source.close();
  }, []);

  const api = useMemo(() => ({ job, artVersion }), [job, artVersion]);
  return <JobsContext.Provider value={api}>{children}</JobsContext.Provider>;
}
