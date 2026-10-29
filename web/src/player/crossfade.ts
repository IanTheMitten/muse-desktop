export const CROSSFADE_KEY = "muse.crossfade";
export const MAX_CROSSFADE_S = 12;
/** Gapless handoff happens this long before the end even with no fade. */
export const GAPLESS_LEAD_MS = 50;

const clampS = (s: number) => Math.round(Math.min(MAX_CROSSFADE_S, Math.max(0, s)));

export function getCrossfadeSeconds(): number {
  try {
    const n = Number(localStorage.getItem(CROSSFADE_KEY));
    return Number.isFinite(n) ? clampS(n) : 0;
  } catch {
    return 0;
  }
}

export function setCrossfadeSeconds(s: number): void {
  try {
    localStorage.setItem(CROSSFADE_KEY, String(clampS(s)));
  } catch {
    // not remembered
  }
}

export function fadeMs(durationMs: number, crossfadeS: number): number {
  return Math.max(0, Math.min(crossfadeS * 1000, durationMs / 2));
}

export function handoffAtMs(durationMs: number, crossfadeS: number): number | null {
  if (!(Number.isFinite(durationMs) && durationMs > 0)) return null;
  return Math.max(0, durationMs - Math.max(fadeMs(durationMs, crossfadeS), GAPLESS_LEAD_MS));
}

/** Equal-power gains for the outgoing and incoming elements. */
export function fadeGains(elapsedMs: number, fade: number): { out: number; in: number } {
  if (fade <= 0) return { out: 0, in: 1 };
  const t = Math.min(1, Math.max(0, elapsedMs / fade));
  return { out: Math.cos((t * Math.PI) / 2), in: Math.sin((t * Math.PI) / 2) };
}

export function handoffClose(positionMs: number): { reason: "track_end"; positionMs: number } {
  return { reason: "track_end", positionMs: Math.round(positionMs) };
}
