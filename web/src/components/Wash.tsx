import { useEffect, useRef, useState } from "react";

import { usePlayer } from "../player/PlayerContext";
import { applyAccent, meshCss, onThemeChange, paletteFor, type Palette } from "../player/wash";

/* The record wash: two stacked layers, the off one is painted and then faded in.
 * Only the "app" instance owns the accent. */
export function Wash({ where }: { where: "app" | "np" }) {
  const { current } = usePlayer();
  const root = useRef<HTMLDivElement>(null);
  const pal = useRef<Palette | null>(null);
  const [tick, setTick] = useState(0);

  const key = current ? (current.album_rowid ?? current.track_rowid) : null;
  const art = current?.has_art ? `/api/tracks/${current.track_rowid}/art` : null;

  useEffect(() => onThemeChange(() => setTick((t) => t + 1)), []);

  // album change: fetch the palette
  useEffect(() => {
    if (key === null || !art) {
      pal.current = null;
      setTick((t) => t + 1);
      return;
    }
    let live = true;
    void paletteFor(key, art).then((p) => {
      if (!live) return;
      pal.current = p;
      setTick((t) => t + 1);
    });
    return () => {
      live = false;
    };
  }, [key, art]);

  // paint on palette or theme change
  useEffect(() => {
    const layers = root.current?.querySelectorAll<HTMLElement>(".wash-layer");
    if (!layers) return;
    const cur = root.current!.querySelector<HTMLElement>(".wash-layer.on");
    const p = pal.current;
    if (!p) {
      cur?.classList.remove("on"); // no record: the ink housing shows
    } else {
      const next = cur === layers[0] ? layers[1] : layers[0];
      next.style.background = meshCss(p.colors, where);
      next.classList.add("on");
      if (cur && cur !== next) cur.classList.remove("on");
    }
    if (where === "app") applyAccent(p);
  }, [tick, where]);

  return (
    <div className="wash" ref={root} aria-hidden="true">
      <div className="wash-layer" />
      <div className="wash-layer" />
    </div>
  );
}

/** A card's own mesh, from its track's cover; undefined until known. */
export function useCardMesh(trackRowid: number | null, hasArt: boolean): string | undefined {
  const [p, setP] = useState<Palette | null>(null);
  const [, setTick] = useState(0);
  useEffect(() => onThemeChange(() => setTick((t) => t + 1)), []);
  useEffect(() => {
    if (trackRowid === null || !hasArt) {
      setP(null);
      return;
    }
    let live = true;
    void paletteFor(-trackRowid, `/api/tracks/${trackRowid}/art`).then((x) => live && setP(x));
    return () => {
      live = false;
    };
  }, [trackRowid, hasArt]);
  return p ? meshCss(p.colors, "card") : undefined;
}
