import { useEffect, useRef, useState } from "react";

import { importPaths } from "../api";
import { dropMessage } from "../dropMessage";
import { shell } from "../shell";
import "../styles/drop.css";

type Result = { text: string; error: boolean };

/* Tauri hands over real paths; HTML5 ondrop never does. */
export function DropZone() {
  const [hover, setHover] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => {
    if (!shell) return;
    let dead = false;
    let off: (() => void) | undefined;
    shell.webview
      .getCurrentWebview()
      .onDragDropEvent(({ payload }) => {
        if (payload.type === "enter" || payload.type === "over") setHover(true);
        else if (payload.type === "leave") setHover(false);
        else {
          setHover(false);
          window.clearTimeout(timer.current);
          importPaths(payload.paths)
            .then((r) => setResult({ text: dropMessage(r), error: false }))
            .catch((e: unknown) =>
              setResult({ text: e instanceof Error ? e.message : "Could not add that", error: true }),
            )
            .finally(() => {
              timer.current = window.setTimeout(() => setResult(null), 2500);
            });
        }
      })
      .then((fn) => (dead ? fn() : (off = fn)));
    return () => {
      dead = true;
      off?.();
      window.clearTimeout(timer.current);
    };
  }, []);

  if (!shell) return null;
  return (
    <div className="drop" data-hover={hover || undefined}>
      <div className="drop-hover" aria-hidden={!hover}>
        <p className="drop-title">Drop to add to your library</p>
        <p className="drop-sub">Folders stay watched. Songs stay where they are.</p>
      </div>
      <div className="drop-result" data-show={result ? true : undefined} aria-live="polite">
        {result && !result.error && result.text}
      </div>
      <div className="drop-result" data-show={result?.error ? true : undefined} role="alert">
        {result?.error && result.text}
      </div>
    </div>
  );
}
