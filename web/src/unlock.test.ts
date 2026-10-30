import { describe, expect, it } from "vitest";
import { isUnlockChord, ownedChart } from "./unlock";

const chord = { ctrlKey: true, shiftKey: true, altKey: true, code: "KeyD" };

describe("isUnlockChord", () => {
  it("ctrl+shift+alt+D", () => expect(isUnlockChord(chord)).toBe(true));
  it("no ctrl", () => expect(isUnlockChord({ ...chord, ctrlKey: false })).toBe(false));
  it("no shift", () => expect(isUnlockChord({ ...chord, shiftKey: false })).toBe(false));
  it("no alt", () => expect(isUnlockChord({ ...chord, altKey: false })).toBe(false));
  it("wrong code", () => expect(isUnlockChord({ ...chord, code: "KeyE" })).toBe(false));
  it("macOS Option key value is ignored", () =>
    expect(isUnlockChord({ ...chord, key: "∂" })).toBe(true));
});

describe("ownedChart", () => {
  it("keeps only rows with a track, in order", () => {
    const a = { id: 1, track: { x: 1 } };
    const b = { id: 2, track: null };
    const c = { id: 3, track: { x: 3 } };
    expect(ownedChart([a, b, c])).toEqual([a, c]);
  });
  it("empty in, empty out", () => expect(ownedChart([])).toEqual([]));
  it("all owned, all kept", () => {
    const rows = [
      { id: 1, track: { x: 1 } },
      { id: 2, track: { x: 2 } },
    ];
    expect(ownedChart(rows)).toEqual(rows);
  });
});
