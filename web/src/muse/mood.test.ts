import { expect, test, vi } from "vitest";
import { toArcSpec } from "./mood";

// mood.ts imports browser-bound modules (window at load); toArcSpec is pure.
vi.mock("../token", () => ({ apiFetch: vi.fn() }));
vi.mock("../api", () => ({ ApiError: class extends Error {} }));

type Spec = { shape: string; sound: string; goal: { darkness: number }; start: { darkness: number } };
const spec = (...args: Parameters<typeof toArcSpec>) => toArcSpec(...args) as Spec;
const p = { v: -0.5, a: 0.9 };

test("toArcSpec default is steady at the point's darkness", () => {
  const s = spec(p, 3, 8);
  expect(s.shape).toBe("steady");
  expect(s.goal.darkness).toBe(0.75);
  expect(s.sound).toBe("energetic");
});

test("toArcSpec lift is a gradual climb to the light", () => {
  const s = spec(p, 3, 8, true);
  expect(s.shape).toBe("gradual");
  expect(s.goal.darkness).toBe(0.25);
  expect(s.start.darkness).toBe(0.75);
});

test("toArcSpec lift goal floors at 0.1", () => {
  expect(spec({ v: 0.8, a: 0 }, 3, 8, true).goal.darkness).toBe(0.1);
});

test("toArcSpec energy off gives sound any", () => {
  expect(spec(p, 3, 8, false, false).sound).toBe("any");
});
