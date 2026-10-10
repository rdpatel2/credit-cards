import { describe, expect, it } from "vitest";
import { overlaps, reconcileLayout, resolveLayoutMove } from "@/lib/layout";
import { homePage, type LayoutItem } from "@/lib/workspace";

function expectLegal(layout: LayoutItem[]) {
  for (const item of layout) {
    expect(item.x).toBeGreaterThanOrEqual(0);
    expect(item.x + item.w).toBeLessThanOrEqual(12);
    expect(item.y).toBeGreaterThanOrEqual(0);
    expect(layout.some((other) => overlaps(item, other))).toBe(false);
  }
}
describe("stable grid placement", () => {
  it("swaps corner widgets into the vacated position without moving the center", () => {
    const original = homePage().layout;
    const next = resolveLayoutMove(original, { ...original[0], x: 9 });
    expect(next.find((l) => l.i === "market")?.x).toBe(9);
    expect(next.find((l) => l.i === "movers")?.x).toBe(0);
    expect(next.find((l) => l.i === "watchlist")).toEqual(original[1]);
    expect(next.find((l) => l.i === "news")).toEqual(original[3]);
    expectLegal(next);
  });
  it("snaps a one-row overlap by one row instead of cascading down the page", () => {
    const original = [
      { i: "a", x: 0, y: 0, w: 6, h: 6 },
      { i: "b", x: 0, y: 8, w: 6, h: 12 },
      { i: "c", x: 6, y: 0, w: 6, h: 20 },
    ];
    const next = resolveLayoutMove(original, { ...original[0], y: 3 });
    expect(next[0].y).toBe(2);
    expect(next.slice(1)).toEqual(original.slice(1));
    expectLegal(next);
  });
  it("moving through neighbors and returning home leaves the entire layout intact", () => {
    const original = homePage().layout;
    resolveLayoutMove(original, { ...original[0], x: 8, y: 3 });
    expect(resolveLayoutMove(original, original[0])).toEqual(original);
  });
  it("resizing nudges the collided neighbor only as far as needed", () => {
    const original = [
      { i: "a", x: 0, y: 0, w: 6, h: 6 },
      { i: "b", x: 0, y: 7, w: 6, h: 6 },
      { i: "c", x: 0, y: 14, w: 6, h: 6 },
    ];
    const next = resolveLayoutMove(
      original,
      { ...original[0], h: 8 },
      "resize",
    );
    expect(next[0].h).toBe(8);
    expect(next[1].y).toBe(8);
    expect(next[2]).toEqual(original[2]);
    expectLegal(next);
  });
  it("keyboard changes use the same minimal placement rules", () => {
    const original = homePage().layout;
    const next = reconcileLayout(
      original,
      original.map((l) => (l.i === "market" ? { ...l, x: 9 } : l)),
    );
    expect(next.find((l) => l.i === "movers")?.x).toBe(0);
    expectLegal(next);
  });
  it("maintains sizes and collision-free bounds across a broad set of drops", () => {
    const original = homePage().layout;
    for (const widget of original) {
      for (let x = 0; x <= 12 - widget.w; x++) {
        for (let y = 0; y <= 22; y++) {
          const next = resolveLayoutMove(original, { ...widget, x, y });
          expectLegal(next);
          expect(next.map((l) => [l.i, l.w, l.h])).toEqual(
            original.map((l) => [l.i, l.w, l.h]),
          );
        }
      }
    }
  });
});
