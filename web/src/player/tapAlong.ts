// Pure tap-along state: one stamp (ms) per lyric line, in order.
export const TAP_LEAD_MS = 150;

export type TapState = { stamps: number[]; total: number };

export function startTap(total: number): TapState {
  return { stamps: [], total };
}

// Stamps lead the tap to cover reaction time and never go backwards.
export function tap(state: TapState, positionMs: number): TapState {
  if (state.stamps.length >= state.total) return state;
  const last = state.stamps[state.stamps.length - 1] ?? 0;
  const stamp = Math.round(Math.max(0, positionMs - TAP_LEAD_MS, last));
  return { ...state, stamps: [...state.stamps, stamp] };
}

export function undo(state: TapState): TapState {
  if (state.stamps.length === 0) return state;
  return { ...state, stamps: state.stamps.slice(0, -1) };
}

export function isDone(state: TapState): boolean {
  return state.stamps.length === state.total;
}

export function cueIndex(state: TapState): number {
  return state.stamps.length;
}
