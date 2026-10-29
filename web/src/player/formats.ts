const MIME: Record<string, string> = {
  m4a: "audio/mp4",
  mp3: "audio/mpeg",
  flac: "audio/flac",
  wav: "audio/wav",
  ogg: "audio/ogg",
  opus: 'audio/ogg; codecs="opus"',
};

const cache = new Map<string, boolean>();

export function canPlayFormat(format: string | null): boolean {
  if (format === null || !(format in MIME)) return true;
  let ok = cache.get(format);
  if (ok === undefined) {
    ok = document.createElement("audio").canPlayType(MIME[format]) !== "";
    cache.set(format, ok);
  }
  return ok;
}

const platformWord = navigator.userAgent.includes("Mac") ? "Mac" : "PC";

export function unplayableReason(track: { playable: boolean; format: string | null }): string | null {
  if (!track.playable) return "File missing";
  if (!canPlayFormat(track.format)) return `Format not supported on this ${platformWord}`;
  return null;
}
