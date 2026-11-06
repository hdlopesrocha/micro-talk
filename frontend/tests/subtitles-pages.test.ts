import { describe, expect, it } from "vitest";
import { cueAtTime, cueIndexAtTime, formatClock } from "../src/utils/subtitles";
import { resolvePageSelection, parsePageList, pageWindow, validatePageSelection } from "../src/utils/pages";
import type { Cue } from "../src/api/types";

const cues: Cue[] = [
  { index: 1, start: 0, end: 1.5, text: "First" },
  { index: 2, start: 1.5, end: 3, text: "Second" },
  { index: 3, start: 4, end: 5.25, text: "Third" },
];

describe("subtitle helpers", () => {
  it("formats clock values", () => {
    expect(formatClock(0)).toBe("0:00");
    expect(formatClock(65.9)).toBe("1:05");
    expect(formatClock(3661)).toBe("1:01:01");
    expect(formatClock(Number.NaN)).toBe("0:00");
  });

  it("finds the cue at a timestamp using half-open intervals", () => {
    expect(cueAtTime(cues, 0)?.text).toBe("First");
    expect(cueAtTime(cues, 1.5)?.text).toBe("Second");
    expect(cueAtTime(cues, 3.5)).toBeNull();
    expect(cueAtTime(cues, 5.25)).toBeNull();
    expect(cueAtTime([], 1)).toBeNull();
  });

  it("returns the matching cue index", () => {
    expect(cueIndexAtTime(cues, 4.5)).toBe(2);
    expect(cueIndexAtTime(cues, 3.7)).toBe(-1);
  });
});

describe("page selection helpers", () => {
  it("resolves all and ranged selections", () => {
    expect(resolvePageSelection({ mode: "all", start: 1, end: 1, selected: [] }, 3)).toEqual([
      1, 2, 3,
    ]);
    expect(
      resolvePageSelection({ mode: "range", start: 3, end: 1, selected: [] }, 5),
    ).toEqual([1, 2, 3]);
  });

  it("deduplicates and sorts custom pages", () => {
    expect(
      resolvePageSelection({ mode: "custom", start: 1, end: 1, selected: [4, 2, 4, 9] }, 10),
    ).toEqual([2, 4, 9]);
  });

  it("parses comma lists and ranges", () => {
    expect(parsePageList("1, 3-5, 9", 10)).toEqual([1, 3, 4, 5, 9]);
    expect(parsePageList("0, 20", 10)).toEqual([1, 10]);
  });

  it("validates empty and out-of-range selections", () => {
    expect(
      validatePageSelection({ mode: "custom", start: 1, end: 1, selected: [] }, 10),
    ).toBe("Select at least one page.");
    expect(
      validatePageSelection({ mode: "range", start: 9, end: 12, selected: [] }, 10),
    ).toContain("between 1 and 10");
    expect(validatePageSelection({ mode: "all", start: 1, end: 1, selected: [] }, 10)).toBeNull();
  });

  it("builds a bounded page window", () => {
    const window = pageWindow(100, 50, 10);
    expect(window.pages[0]).toBe(45);
    expect(window.pages.at(-1)).toBe(54);
    expect(window.hasPrev).toBe(true);
    expect(window.hasNext).toBe(true);
    expect(pageWindow(3, 2, 10).pages).toEqual([1, 2, 3]);
  });
});
