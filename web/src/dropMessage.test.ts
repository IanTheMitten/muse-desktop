import { describe, expect, it } from "vitest";
import { dropMessage } from "./dropMessage";

const r = (folders_added: number, songs_added: number, already: number, ignored: number) => ({
  folders_added,
  songs_added,
  already,
  ignored,
});

describe("dropMessage", () => {
  it("one folder", () => expect(dropMessage(r(1, 0, 0, 0))).toBe("Added 1 folder"));
  it("folders and songs", () =>
    expect(dropMessage(r(2, 3, 0, 0))).toBe("Added 2 folders and 3 songs"));
  it("one song", () => expect(dropMessage(r(0, 1, 0, 0))).toBe("Added 1 song"));
  it("already there", () =>
    expect(dropMessage(r(0, 0, 2, 0))).toBe("Already in your library"));
  it("nothing to add", () =>
    expect(dropMessage(r(0, 0, 0, 1))).toBe(
      "Nothing to add. Muse plays MP3, M4A, FLAC, WAV, OGG and Opus files.",
    ));
  it("added wins over already and ignored", () =>
    expect(dropMessage(r(1, 0, 1, 1))).toBe("Added 1 folder"));
});
