/* Recording what was actually listened to.
 *
 * This is the part of slice 1 that manufactures slice 2's training data, and
 * that data is the only thing in the system that cannot be regenerated. So the
 * client's job here is narrow: report physical fact, and report the reason a
 * span ended exactly as it happened. It never decides what any of it means —
 * `skip` and `full_listen` are a pure function on the server, retunable later
 * over the whole history (ADR 0001).
 *
 * Every call is best-effort. A failed heartbeat must never interrupt playback:
 * the worst case is a span the sweeper closes as `lost`, which is a truthful
 * record of a client that stopped reporting. */

export type EndedReason =
  | "track_end"
  | "user_next"
  | "user_prev"
  | "new_selection"
  | "pause_timeout";

export async function openSpan(
  trackRowid: number,
  durationMs: number,
  source: string,
): Promise<number | null> {
  try {
    const response = await fetch("/api/plays", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        track_rowid: trackRowid,
        duration_ms: Math.round(durationMs),
        source,
      }),
    });
    if (!response.ok) return null;
    return ((await response.json()) as { id: number }).id;
  } catch {
    return null;
  }
}

export async function beatSpan(
  playId: number,
  positionMs: number,
  seekCount: number,
): Promise<void> {
  try {
    await fetch(`/api/plays/${playId}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        position_ms: Math.round(positionMs),
        seek_count: seekCount,
      }),
    });
  } catch {
    /* the sweeper will close it as lost, which is the truth */
  }
}

export async function closeSpan(
  playId: number,
  reason: EndedReason,
  positionMs: number,
): Promise<void> {
  try {
    await fetch(`/api/plays/${playId}/close`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        ended_reason: reason,
        position_ms: Math.round(positionMs),
      }),
      // Must survive the tab going away, or every close becomes a `lost`.
      keepalive: true,
    });
  } catch {
    /* same */
  }
}

/** How long a pause has to last before it stops being a pause.
 *
 * Long enough that stepping away from the desk for a moment is not recorded as
 * abandoning the track, short enough that the reason is still true. */
export const PAUSE_TIMEOUT_MS = 60_000;
