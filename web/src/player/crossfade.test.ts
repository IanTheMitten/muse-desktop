import { afterEach, expect, test, vi } from "vitest";
import {
  fadeGains,
  fadeMs,
  getCrossfadeSeconds,
  handoffAtMs,
  handoffClose,
  setCrossfadeSeconds,
} from "./crossfade";

function stub(initial?: string) {
  const m = new Map<string, string>();
  if (initial !== undefined) m.set("muse.crossfade", initial);
  vi.stubGlobal("localStorage", {
    getItem: (k: string) => m.get(k) ?? null,
    setItem: (k: string, v: string) => void m.set(k, v),
  });
  return m;
}

afterEach(() => vi.unstubAllGlobals());

test("getCrossfadeSeconds", () => {
  stub();
  expect(getCrossfadeSeconds()).toBe(0);
  stub("6");
  expect(getCrossfadeSeconds()).toBe(6);
  stub("20");
  expect(getCrossfadeSeconds()).toBe(12);
  stub("-3");
  expect(getCrossfadeSeconds()).toBe(0);
  stub("abc");
  expect(getCrossfadeSeconds()).toBe(0);
  vi.stubGlobal("localStorage", {
    getItem: () => {
      throw new Error("denied");
    },
  });
  expect(getCrossfadeSeconds()).toBe(0);
});

test("setCrossfadeSeconds rounds and clamps", () => {
  let m = stub();
  setCrossfadeSeconds(7.6);
  expect(m.get("muse.crossfade")).toBe("8");
  m = stub();
  setCrossfadeSeconds(15);
  expect(m.get("muse.crossfade")).toBe("12");
  m = stub();
  setCrossfadeSeconds(-1);
  expect(m.get("muse.crossfade")).toBe("0");
});

test("fadeMs", () => {
  expect(fadeMs(200000, 6)).toBe(6000);
  expect(fadeMs(8000, 6)).toBe(4000);
  expect(fadeMs(200000, 0)).toBe(0);
});

test("handoffAtMs", () => {
  expect(handoffAtMs(200000, 0)).toBe(199950);
  expect(handoffAtMs(200000, 6)).toBe(194000);
  expect(handoffAtMs(8000, 6)).toBe(4000);
  expect(handoffAtMs(30, 0)).toBe(0);
  expect(handoffAtMs(0, 6)).toBeNull();
  expect(handoffAtMs(NaN, 6)).toBeNull();
});

test("fadeGains is equal-power", () => {
  expect(fadeGains(0, 6000)).toEqual({ out: 1, in: 0 });
  const end = fadeGains(6000, 6000);
  expect(end.out).toBeCloseTo(0);
  expect(end.in).toBe(1);
  const mid = fadeGains(3000, 6000);
  expect(mid.out).toBeCloseTo(Math.SQRT1_2);
  expect(mid.in).toBeCloseTo(Math.SQRT1_2);
  expect(fadeGains(9000, 6000)).toEqual(end);
  expect(fadeGains(100, 0)).toEqual({ out: 0, in: 1 });
});

test("handoffClose", () => {
  expect(handoffClose(194000.4)).toEqual({ reason: "track_end", positionMs: 194000 });
});
