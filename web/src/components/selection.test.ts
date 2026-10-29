import { expect, test } from "vitest";
import { nextSelection } from "./selection";

const ids = [1, 2, 3, 4, 5];
const none = { shift: false, meta: false };

test("meta toggles and sets the anchor", () => {
  const a = nextSelection(new Set(), null, ids, 2, { shift: false, meta: true });
  expect([...a.selected]).toEqual([2]);
  expect(a.anchor).toBe(2);
  const b = nextSelection(a.selected, a.anchor, ids, 2, { shift: false, meta: true });
  expect(b.selected.size).toBe(0);
});

test("shift with an anchor adds the inclusive range", () => {
  const r = nextSelection(new Set([4]), 4, ids, 2, { shift: true, meta: false });
  expect([...r.selected].sort()).toEqual([2, 3, 4]);
  expect(r.anchor).toBe(4);
});

test("plain click with a selection toggles", () => {
  const r = nextSelection(new Set([1]), 1, ids, 3, none);
  expect([...r.selected].sort()).toEqual([1, 3]);
  expect(r.handled).toBe(true);
});

test("plain click with no selection is not handled", () => {
  expect(nextSelection(new Set(), null, ids, 3, none).handled).toBe(false);
});

test("shift without an anchor acts like meta", () => {
  const r = nextSelection(new Set(), null, ids, 3, { shift: true, meta: false });
  expect([...r.selected]).toEqual([3]);
  expect(r.anchor).toBe(3);
});
