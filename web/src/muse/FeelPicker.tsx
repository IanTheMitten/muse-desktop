import { useRef, useState, type CSSProperties, type KeyboardEvent, type PointerEvent } from "react";

import type { MoodPoint } from "../player/PlayerContext";
import { EMO, PRESETS, activePreset, blend, feelWords, wordsForPoint } from "./mood";
import "../styles/muse.css";

/* "How are you feeling?" as one self-contained unit: preset chips, the mood
 * map, and the fine-tune sliders. Change the controls here and every place
 * that asks the question (the Muse tab, Make a playlist) changes with it.
 *
 * The caller owns the mood point — the Muse tab hands it to the player, the
 * playlist maker keeps it local — so asking the question never steers
 * playback by itself. */

const S = 200;
const clamp = (x: number) => Math.max(-1, Math.min(1, x));

function storedLevels(key: string): Record<string, number> {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as Record<string, number>) : {};
  } catch {
    return {};
  }
}

export type Feel = ReturnType<typeof useFeel>;

export function useFeel(
  storageKey: string,
  point: MoodPoint | null,
  onPoint: (p: MoodPoint | null) => void,
) {
  const [levels, setLevels] = useState(() => storedLevels(storageKey));
  /* "map" once the dot was dragged: the readout then names the point, not the sliders. */
  const [byMap, setByMap] = useState(() => {
    const b = blend(storedLevels(storageKey));
    return Boolean(point) && !(b && point && Math.abs(b.v - point.v) < 0.01 && Math.abs(b.a - point.a) < 0.01);
  });

  const applyLevels = (next: Record<string, number>) => {
    setLevels(next);
    setByMap(false);
    try { localStorage.setItem(storageKey, JSON.stringify(next)); } catch { /* private mode */ }
    onPoint(blend(next));
  };
  const setPoint = (p: MoodPoint) => {
    setByMap(true);
    onPoint({ v: clamp(p.v), a: clamp(p.a) });
  };
  const clear = () => {
    setLevels({});
    setByMap(false);
    try { localStorage.removeItem(storageKey); } catch { /* private mode */ }
    onPoint(null);
  };

  return {
    levels,
    point,
    dot: point ?? { v: 0, a: 0 },
    readout: point && byMap ? wordsForPoint(point) : feelWords(levels),
    preset: activePreset(levels),
    applyLevels,
    setPoint,
    clear,
  };
}

export function FeelPicker({ feel, id = "feel" }: { feel: Feel; id?: string }) {
  const { levels, dot, readout, preset, applyLevels, setPoint } = feel;
  const [fine, setFine] = useState(false);
  const bg = useRef<SVGRectElement>(null);
  const x = ((dot.v + 1) / 2) * S;
  const y = (1 - (dot.a + 1) / 2) * S;

  const fromPointer = (e: PointerEvent<SVGSVGElement>) => {
    const r = bg.current?.getBoundingClientRect();
    if (!r || !r.width) return;
    setPoint({ v: ((e.clientX - r.left) / r.width) * 2 - 1, a: 1 - ((e.clientY - r.top) / r.height) * 2 });
  };
  const onKey = (e: KeyboardEvent) => {
    const d = { ArrowLeft: [-0.1, 0], ArrowRight: [0.1, 0], ArrowUp: [0, 0.1], ArrowDown: [0, -0.1] }[e.key];
    if (!d) return;
    e.preventDefault();
    setPoint({ v: dot.v + d[0], a: dot.a + d[1] });
  };

  return (
    <div className="feel-picker">
      <div className="chips" role="group" aria-label="Quick moods">
        {Object.keys(PRESETS).map((n) => (
          <button key={n} type="button" className="chip" aria-pressed={n === preset} onClick={() => applyLevels({ ...PRESETS[n] })}>
            {n}
          </button>
        ))}
      </div>
      <div className="feel">
        <figure className="moodmap">
          <svg
            viewBox={`-34 -22 ${S + 68} ${S + 44}`}
            role="application"
            tabIndex={0}
            aria-label={readout}
            onPointerDown={(e) => { e.currentTarget.setPointerCapture(e.pointerId); fromPointer(e); }}
            onPointerMove={(e) => { if (e.currentTarget.hasPointerCapture(e.pointerId)) fromPointer(e); }}
            onKeyDown={onKey}
          >
            <rect ref={bg} x="0" y="0" width={S} height={S} rx="14" className="map-bg" />
            <line className="grid-l" x1={S / 2} x2={S / 2} y1="0" y2={S} />
            <line className="grid-l" x1="0" x2={S} y1={S / 2} y2={S / 2} />
            {EMO.filter((e) => e.k === "angry" || e.k === "romantic").map((e) => (
              <text key={e.k} x={((e.v + 1) / 2) * S} y={(1 - (e.a + 1) / 2) * S + 4} textAnchor="middle" className="emo-l">{e.name}</text>
            ))}
            <g className="pt" style={{ transform: `translate(${x}px, ${y}px)` }}>
              <circle className="halo" r="22" />
              <circle className="dot" r="9" />
            </g>
            <text x={S / 2} y="-8" textAnchor="middle">Energetic</text>
            <text x={S / 2} y={S + 18} textAnchor="middle">Calm</text>
            <text x="-8" y={S / 2 + 4} textAnchor="end">Sad</text>
            <text x={S + 8} y={S / 2 + 4}>Happy</text>
          </svg>
        </figure>
        <div>
          <button type="button" className="link-btn" aria-expanded={fine} aria-controls={`${id}-emo`} onClick={() => setFine(!fine)}>
            Fine-tune
          </button>
          <div className="emo-grid" id={`${id}-emo`} hidden={!fine}>
            {EMO.map((e) => (
              <div className="ctl emo" key={e.k}>
                <div className="ctl-head">
                  <label htmlFor={`${id}-${e.k}`}>{e.name}</label>
                  <output htmlFor={`${id}-${e.k}`}>{levels[e.k] || 0}</output>
                </div>
                <input
                  className="range" type="range" id={`${id}-${e.k}`} min={0} max={100} step={5}
                  value={levels[e.k] || 0}
                  style={{ "--p": `${levels[e.k] || 0}%` } as CSSProperties}
                  onChange={(ev) => applyLevels({ ...levels, [e.k]: Number(ev.target.value) })}
                />
              </div>
            ))}
          </div>
        </div>
      </div>
      <p className="feel-read" role="status">{readout}</p>
    </div>
  );
}
