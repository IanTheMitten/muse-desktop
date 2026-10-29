import { ApiError } from "../api";
import type { MoodPoint } from "../player/PlayerContext";

export const EMO = [
  { k: "happy", name: "Happy", v: 0.8, a: 0.45 }, { k: "sad", name: "Sad", v: -0.75, a: -0.4 },
  { k: "calm", name: "Calm", v: 0.35, a: -0.75 }, { k: "energetic", name: "Energetic", v: 0.45, a: 0.9 },
  { k: "angry", name: "Angry", v: -0.65, a: 0.8 }, { k: "romantic", name: "Romantic", v: 0.6, a: -0.15 },
];

export const PRESETS: Record<string, Record<string, number>> = {
  "Heartbroken": { sad: 85, romantic: 40, calm: 30 }, "Chill": { calm: 80, happy: 35 },
  "Hype": { energetic: 90, happy: 60 }, "Focus": { calm: 60, energetic: 30 },
  "Feel good": { happy: 85, energetic: 45 }, "Late night": { calm: 55, sad: 35, romantic: 45 },
  "Angry": { angry: 85, energetic: 60 }, "In love": { romantic: 90, happy: 50 },
};

const lvl = (levels: Record<string, number>, k: string) => levels[k] || 0;

export function blend(levels: Record<string, number>): MoodPoint | null {
  const tot = EMO.reduce((s, e) => s + lvl(levels, e.k), 0);
  if (!tot) return null;
  return {
    v: EMO.reduce((s, e) => s + e.v * lvl(levels, e.k), 0) / tot,
    a: EMO.reduce((s, e) => s + e.a * lvl(levels, e.k), 0) / tot,
  };
}

export function feelWords(levels: Record<string, number>): string {
  const top = EMO.filter((e) => lvl(levels, e.k) > 0).sort((x, y) => lvl(levels, y.k) - lvl(levels, x.k));
  if (!top.length) return "Nothing set. Muse plays close to what's on.";
  return top.length === 1 || lvl(levels, top[1].k) < 25
    ? `Mostly ${top[0].name.toLowerCase()}`
    : `${top[0].name}, with some ${top[1].name.toLowerCase()}`;
}

export const activePreset = (levels: Record<string, number>): string | undefined =>
  Object.keys(PRESETS).find((n) => EMO.every((e) => (PRESETS[n][e.k] || 0) === lvl(levels, e.k)));

export function wordsForPoint(p: MoodPoint): string {
  const [a, b] = EMO.map((e) => ({ e, d: Math.hypot(e.v - p.v, e.a - p.a) })).sort((x, y) => x.d - y.d);
  return b.d > 2 * a.d
    ? `Mostly ${a.e.name.toLowerCase()}`
    : `${a.e.name}, with some ${b.e.name.toLowerCase()}`;
}

export function toArcSpec(p: MoodPoint, seed: number | null, n: number): object {
  const darkness = Math.round(((1 - p.v) / 2) * 100) / 100;
  const sound = p.a <= -0.33 ? "calm" : p.a >= 0.33 ? "energetic" : "mid";
  return {
    version: 1,
    kind: "mood",
    seed,
    start: { subject: null, darkness },
    goal: { subject: null, darkness },
    shape: "steady",
    sound,
    length: n,
    limits: "anything",
  };
}

export async function postArcPlaylist(spec: object, n: number): Promise<number[]> {
  const response = await fetch("/api/arc", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ spec, mode: "playlist", n }),
  });
  if (!response.ok) throw new ApiError("/arc", response.status, response.statusText);
  const body = (await response.json()) as { results: { track_rowid: number }[] };
  return body.results.map((r) => r.track_rowid);
}
