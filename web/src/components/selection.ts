import { useCallback, useEffect, useRef, useState } from "react";

type Keys = { shift: boolean; meta: boolean };

export function nextSelection(
  selected: Set<number>,
  anchor: number | null,
  rowids: number[],
  rowid: number,
  keys: Keys,
): { selected: Set<number>; anchor: number | null; handled: boolean } {
  const toggle = () => {
    const next = new Set(selected);
    if (!next.delete(rowid)) next.add(rowid);
    return { selected: next, anchor: rowid, handled: true };
  };
  if (keys.shift && anchor !== null) {
    const a = rowids.indexOf(anchor);
    const b = rowids.indexOf(rowid);
    if (a >= 0 && b >= 0) {
      const next = new Set(selected);
      for (const id of rowids.slice(Math.min(a, b), Math.max(a, b) + 1)) next.add(id);
      return { selected: next, anchor, handled: true };
    }
  }
  if (keys.shift || keys.meta) return toggle();
  if (selected.size > 0) return toggle();
  return { selected, anchor, handled: false };
}

export function useSelection(rowids: number[]) {
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const anchor = useRef<number | null>(null);
  const latest = useRef({ selected, rowids });
  latest.current = { selected, rowids };

  const clear = useCallback(() => {
    anchor.current = null;
    setSelected((s) => (s.size ? new Set() : s));
  }, []);

  // Callers pass a memoized array, so this fires when the list really changes.
  useEffect(clear, [rowids, clear]);

  useEffect(() => {
    if (selected.size === 0) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !document.querySelector("dialog[open]")) clear();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selected.size, clear]);

  const onSelect = useCallback((event: React.MouseEvent, rowid: number) => {
    const r = nextSelection(latest.current.selected, anchor.current, latest.current.rowids, rowid, {
      shift: event.shiftKey,
      meta: event.metaKey || event.ctrlKey,
    });
    if (!r.handled) return false;
    anchor.current = r.anchor;
    setSelected(r.selected);
    return true;
  }, []);

  return { selected, onSelect, clear };
}
