import { apiFetch } from "../token";
import type { Track } from "../api";

/* Talking to the server-side player state.
 *
 * The queue lives on the server so it follows the listener between the laptop
 * and the phone. Audio always plays locally on whichever device is open —
 * there is no remote control and no takeover. */

export const HEARTBEAT_MS = 10_000;

export type RemoteState = {
  queue: Track[];
  queue_index: number;
  position_ms: number;
  shuffle: number;
  repeat_mode: RepeatMode;
  /** The track the current radio session started from, or null when radio is
   *  off. It rides the same guarded heartbeat as shuffle and repeat so radio
   *  follows the listener between devices the way the other two modes do. */
  radio_seed: number | null;
  /** Queued rowids radio put there, so their plays are tagged `radio` after a
   *  reload or on the other device as well as in the tab that queued them. */
  radio_added: number[];
  updated_at: number;
  updated_by: string | null;
};

export type RepeatMode = "off" | "one" | "all";

export type ClientState = "playing" | "paused" | "idle";

/** A stable label for this device, so `updated_by` says something useful when
 * a position looks wrong. Debugging only — nothing keys on it. */
export function deviceLabel(): string {
  const key = "muse.device";
  let label = localStorage.getItem(key);
  if (!label) {
    const platform = /iPhone|iPad|Android/.test(navigator.userAgent)
      ? "phone"
      : "desktop";
    label = `${platform}-${Math.random().toString(36).slice(2, 6)}`;
    localStorage.setItem(key, label);
  }
  return label;
}

export async function readState(signal?: AbortSignal): Promise<RemoteState> {
  const response = await apiFetch("/api/player/state", { signal });
  if (!response.ok) throw new Error(`player state -> ${response.status}`);
  return (await response.json()) as RemoteState;
}

export async function writeState(payload: {
  queue: number[];
  queue_index: number;
  position_ms: number;
  client_state: ClientState;
  shuffle: boolean;
  repeat_mode: RepeatMode;
  radio_seed: number | null;
  radio_added: number[];
}): Promise<void> {
  // Fire and forget. A dropped heartbeat costs at most ten seconds of
  // position, and blocking playback on a failed write would be far worse than
  // the thing it protects against.
  try {
    await apiFetch("/api/player/state", {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ ...payload, updated_by: deviceLabel() }),
      keepalive: true,
    });
  } catch {
    /* offline, or the tab is going away */
  }
}
