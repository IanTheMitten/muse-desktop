import { expect, test } from "vitest";
import { append, insertNext, move, nextIndex, removeAt, upcomingIndex } from "./queue";

const off = { shuffle: false, repeat: "off" as const };

test("insertNext places tracks after the current index", () => {
  expect(insertNext(["a", "b", "c"], 0, ["x", "y"])).toEqual(["a", "x", "y", "b", "c"]);
  expect(insertNext(["a", "b"], 1, ["x"])).toEqual(["a", "b", "x"]);
});

test("append adds to the end", () => {
  expect(append(["a"], ["x", "y"])).toEqual(["a", "x", "y"]);
});

test("removeAt removes a slot, refusing the current and out-of-range", () => {
  expect(removeAt(["a", "b", "c"], 0, 2)).toEqual(["a", "b"]);
  const q = ["a", "b", "c"];
  expect(removeAt(q, 0, 0)).toBe(q);
  expect(removeAt(q, 0, 5)).toBe(q);
});

test("move reorders, refusing the current slot and no-ops", () => {
  expect(move(["a", "b", "c", "d"], 0, 1, 3)).toEqual(["a", "c", "d", "b"]);
  expect(move(["a", "b", "c", "d"], 0, 3, 1)).toEqual(["a", "d", "b", "c"]);
  const q = ["a", "b", "c", "d"];
  expect(move(q, 1, 0, 2)).toBe(q);
  expect(move(q, 1, 2, 0)).toBe(q);
  expect(move(q, 0, 2, 2)).toBe(q);
});

test("nextIndex", () => {
  expect(nextIndex(0, 0, off)).toBe(0);
  expect(nextIndex(3, 0, off)).toBe(1);
  expect(nextIndex(3, 2, off)).toBe(2);
  expect(nextIndex(3, 2, { shuffle: false, repeat: "all" })).toBe(0);
  const vals = [0.4, 0.9];
  expect(nextIndex(3, 1, { shuffle: true, repeat: "off" }, () => vals.shift()!)).toBe(2);
  expect(nextIndex(1, 0, { shuffle: true, repeat: "off" })).toBe(0);
});

test("upcomingIndex", () => {
  expect(upcomingIndex(3, 0, off)).toBe(1);
  expect(upcomingIndex(3, 2, off)).toBeNull();
  expect(upcomingIndex(3, 2, { shuffle: false, repeat: "all" })).toBe(0);
  expect(upcomingIndex(1, 0, { shuffle: false, repeat: "all" })).toBeNull();
  expect(upcomingIndex(3, 0, { shuffle: true, repeat: "off" })).toBeNull();
  expect(upcomingIndex(3, 0, { shuffle: false, repeat: "one" })).toBeNull();
});
