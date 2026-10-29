import { expect, test } from "vitest";
import { cueIndex, isDone, startTap, tap, undo } from "./tapAlong";

test("startTap begins empty", () => {
  expect(startTap(3)).toEqual({ stamps: [], total: 3 });
});

test("taps lead the position and never go backwards", () => {
  let s = startTap(3);
  s = tap(s, 1000);
  s = tap(s, 900);
  s = tap(s, 5000);
  expect(s.stamps).toEqual([850, 850, 4850]);
});

test("an early tap clamps to zero", () => {
  expect(tap(startTap(2), 100).stamps).toEqual([0]);
});

test("a tap past total is a no-op", () => {
  const s = tap(startTap(1), 1000);
  expect(tap(s, 2000)).toBe(s);
});

test("undo drops the last stamp, no-op when empty", () => {
  const empty = startTap(3);
  expect(undo(empty)).toBe(empty);
  let s = tap(tap(empty, 1000), 2000);
  s = undo(undo(s));
  expect(s.stamps).toEqual([]);
  expect(tap(s, 3000).stamps).toEqual([2850]);
});

test("isDone and cueIndex track progress", () => {
  let s = startTap(2);
  expect(isDone(s)).toBe(false);
  expect(cueIndex(s)).toBe(0);
  s = tap(tap(s, 1000), 2000);
  expect(isDone(s)).toBe(true);
  expect(cueIndex(s)).toBe(2);
});
