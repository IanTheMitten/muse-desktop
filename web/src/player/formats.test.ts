import { expect, test, vi } from "vitest";

vi.stubGlobal("document", {
  createElement: () => ({ canPlayType: (m: string) => (m === "audio/flac" ? "" : "maybe") }),
});
vi.stubGlobal("navigator", { userAgent: "Linux" });
const { playableOnly, unplayableReason } = await import("./formats");

test("missing file", () => {
  expect(unplayableReason({ playable: false, format: "mp3" })).toBe("File missing");
});

test("unsupported format", () => {
  expect(unplayableReason({ playable: true, format: "flac" })).toMatch(/^Format not supported on this /);
});

test("supported or unknown format", () => {
  expect(unplayableReason({ playable: true, format: "mp3" })).toBeNull();
  expect(unplayableReason({ playable: true, format: null })).toBeNull();
});

test("playableOnly keeps playable tracks in order", () => {
  const a = { playable: true, format: "mp3" };
  const b = { playable: false, format: "mp3" };
  const c = { playable: true, format: "flac" };
  const d = { playable: true, format: null };
  expect(playableOnly([a, b, c, d])).toEqual([a, d]);
});
