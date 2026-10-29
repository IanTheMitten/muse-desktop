import { useEffect, useState } from "react";

type State<T> = {
  data: T | null;
  error: string | null;
  loading: boolean;
};

/** Fetch on mount and whenever `deps` change, aborting the previous request.
 *
 * Aborting matters more than it looks: tapping through artists quickly on a
 * phone starts several requests, and without this the slowest one wins and the
 * screen shows the wrong artist. */
export function useLoad<T>(
  load: (signal: AbortSignal) => Promise<T>,
  deps: unknown[],
): State<T> {
  const [state, setState] = useState<State<T>>({
    data: null,
    error: null,
    loading: true,
  });

  useEffect(() => {
    const abort = new AbortController();
    setState((previous) => ({ ...previous, loading: true, error: null }));

    load(abort.signal)
      .then((data) => {
        if (!abort.signal.aborted) setState({ data, error: null, loading: false });
      })
      .catch((cause: unknown) => {
        if (abort.signal.aborted) return;
        setState({
          data: null,
          error: cause instanceof Error ? cause.message : String(cause),
          loading: false,
        });
      });

    return () => abort.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return state;
}
