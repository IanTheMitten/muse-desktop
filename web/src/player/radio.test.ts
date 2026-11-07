import { expect, test } from "vitest";
import { radioPath, radioBody, relaxNote, UNTAGGED_NOTE, NO_VECTORS_NOTE } from "./radio";

test("radioPath builds the query with lift, recent and exclude", () => {
  expect(radioPath({ seed: 7, n: 1, pos: 3, recent: [9, 8], exclude: [1, 2], lift: true })).toBe(
    "/arc/7?mode=radio&n=1&pos=3&lift=1&recent=9%2C8&exclude=1%2C2",
  );
});

test("radioPath lift false gives lift=0", () => {
  expect(radioPath({ seed: 7, n: 1, pos: 3, recent: [9], exclude: [1], lift: false })).toContain("lift=0");
});

test("radioPath omits empty recent and exclude", () => {
  const path = radioPath({ seed: 7, n: 1, pos: 3, recent: [], exclude: [], lift: true });
  expect(path).not.toContain("recent");
  expect(path).not.toContain("exclude");
});

test("radioBody shapes the POST body", () => {
  expect(radioBody({ spec: { a: 1 }, n: 8, pos: 2, recent: [9], exclude: [1] })).toEqual({
    spec: { a: 1 },
    mode: "radio",
    n: 8,
    pos: 2,
    recent: [9],
    exclude: [1],
  });
});

test("relaxNote words the relaxation steps", () => {
  expect(relaxNote([])).toBeNull();
  expect(relaxNote(["filler"])).toBe("Running low: played a song without lyric tags.");
  expect(relaxNote(["filler", "freshness", "artist_spacing"])).toBe(
    "Running low: played a song without lyric tags, ignored freshness, allowed a repeated artist.",
  );
  expect(relaxNote(["bogus"])).toBeNull();
  expect(relaxNote(["freshness", "bogus"])).toBe("Running low: ignored freshness.");
});

test("fixed notes", () => {
  expect(UNTAGGED_NOTE).toBe("This song has no lyric tags, so Radio follows its sound.");
  expect(NO_VECTORS_NOTE).toBe("Muse hasn't listened to this song yet.");
});
