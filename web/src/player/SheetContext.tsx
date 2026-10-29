import { createContext, useCallback, useContext, useMemo } from "react";
import type { ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";

/* Whether the player overlay is up, and which panel it shows.
 *
 * The overlay is router state, not component state: opening pushes one history
 * entry (`state.np`), and every way out (chevron, Esc, drag, the phone's back
 * gesture) is the same `history.back()`. One close model, and the page behind
 * never moves because it never navigated.
 *
 * "art" means no panel: on a phone the record fills the screen. At 900px and up
 * a panel is always visible, so "art" reads as the queue there.
 */

export type SheetPanel = "lyrics" | "queue";
export type SheetOpen = "art" | SheetPanel;

type SheetApi = {
  open: boolean;
  panel: SheetOpen;
  show: (panel?: SheetPanel) => void;
  hide: () => void;
  setPanel: (panel: SheetOpen) => void;
};

const Sheet = createContext<SheetApi | null>(null);

export function SheetProvider({ children }: { children: ReactNode }) {
  const loc = useLocation();
  const navigate = useNavigate();
  const np = (loc.state as { np?: SheetOpen } | null)?.np;
  const open = Boolean(np);
  const panel: SheetOpen = np ?? "art";

  const here = loc.pathname + loc.search;
  const show = useCallback(
    (next?: SheetPanel) =>
      navigate(here, {
        replace: open, // already up: switching panels never pushes
        state: { ...(loc.state as object | null), np: next ?? "art" },
      }),
    [navigate, here, loc.state, open],
  );
  const hide = useCallback(() => {
    if (window.history.state?.idx > 0) navigate(-1);
    else navigate(here, { replace: true, state: {} });
  }, [navigate, here]);
  const setPanel = useCallback(
    (next: SheetOpen) =>
      navigate(here, { replace: true, state: { ...(loc.state as object | null), np: next } }),
    [navigate, here, loc.state],
  );

  const value = useMemo(
    () => ({ open, panel, show, hide, setPanel }),
    [open, panel, show, hide, setPanel],
  );
  return <Sheet.Provider value={value}>{children}</Sheet.Provider>;
}

export function useSheet(): SheetApi {
  const api = useContext(Sheet);
  if (!api) throw new Error("useSheet outside SheetProvider");
  return api;
}
