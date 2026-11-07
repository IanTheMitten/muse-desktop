export type RadioQuery = {
  seed: number;
  n: number;
  pos: number;
  recent: number[];
  exclude: number[];
  lift: boolean;
};

export function radioPath(q: RadioQuery): string {
  const params = new URLSearchParams({
    mode: "radio",
    n: String(q.n),
    pos: String(q.pos),
    lift: q.lift ? "1" : "0",
  });
  if (q.recent.length) params.set("recent", q.recent.join(","));
  if (q.exclude.length) params.set("exclude", q.exclude.join(","));
  return `/arc/${q.seed}?${params}`;
}

export function radioBody(b: {
  spec: object;
  n: number;
  pos: number;
  recent: number[];
  exclude: number[];
}): object {
  return { spec: b.spec, mode: "radio", n: b.n, pos: b.pos, recent: b.recent, exclude: b.exclude };
}

export const RELAX_COPY: Record<string, string> = {
  filler: "played a song without lyric tags",
  freshness: "ignored freshness",
  artist_spacing: "allowed a repeated artist",
};

export function relaxNote(rungs: string[]): string | null {
  const known = rungs.filter((r) => r in RELAX_COPY).map((r) => RELAX_COPY[r]);
  return known.length ? `Running low: ${known.join(", ")}.` : null;
}

export const UNTAGGED_NOTE = "This song has no lyric tags, so Radio follows its sound.";
export const NO_VECTORS_NOTE = "Muse hasn't listened to this song yet.";
