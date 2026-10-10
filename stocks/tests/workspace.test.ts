import { describe, expect, it } from "vitest";
import {
  addWidget,
  homePage,
  initialWorkspace,
  parseWorkspace,
  removeWidget,
  resolvePage,
  settleLayout,
  symbolSchema,
  workspaceSchema,
} from "@/lib/workspace";

describe("workspace persistence and invariants", () => {
  it("resolves overlaps introduced by keyboard resizing without moving unrelated columns", () => {
    const settled = settleLayout([
      { i: "one", x: 0, y: 0, w: 8, h: 7 },
      { i: "two", x: 4, y: 0, w: 4, h: 7 },
      { i: "three", x: 8, y: 0, w: 4, h: 7 },
    ]);
    expect(settled.map((item) => item.y)).toEqual([0, 7, 0]);
    expect(settleLayout(homePage().layout)).toEqual(homePage().layout);
  });
  it("round-trips the default five-section workspace", () => {
    const state = initialWorkspace();
    expect(parseWorkspace(JSON.stringify(state))).toEqual(state);
    expect(state.pages[0].widgets).toHaveLength(5);
    const center = state.pages[0].layout.find((l) => l.i === "watchlist")!;
    expect(center).toMatchObject({ x: 3, w: 6, h: 14 });
  });
  it("adds and removes a widget without leaving orphan grid entries", () => {
    const page = addWidget(homePage(), "notes");
    const widget = page.widgets.at(-1)!;
    expect(page.layout.at(-1)!.y).toBe(14);
    expect(removeWidget(page, widget.id)).toEqual(homePage());
  });
  it("rejects invalid, oversized, or unsupported imports", () => {
    expect(() => parseWorkspace("not json")).toThrow();
    expect(() => parseWorkspace("x".repeat(2_000_001))).toThrow(/large/);
    expect(() =>
      parseWorkspace(JSON.stringify({ ...initialWorkspace(), version: 2 })),
    ).toThrow(/Invalid/);
  });
  it("rejects duplicate widget identities, missing home, and orphan watchlists", () => {
    const state = initialWorkspace();
    state.pages[0].widgets[1].id = state.pages[0].widgets[0].id;
    expect(workspaceSchema.safeParse(state).success).toBe(false);
    const noHome = initialWorkspace();
    noHome.pages[0].id = "other";
    expect(workspaceSchema.safeParse(noHome).success).toBe(false);
    const orphan = initialWorkspace();
    orphan.stockTemplate.widgets[0].watchlistId = "missing";
    expect(workspaceSchema.safeParse(orphan).success).toBe(false);
  });
  it("rejects grid geometry outside the twelve-column canvas", () => {
    const state = initialWorkspace();
    state.pages[0].layout[0] = { i: "market", x: 10, y: 0, w: 5, h: 7 };
    expect(workspaceSchema.safeParse(state).success).toBe(false);
  });
  it("shares stock templates until a ticker gets an independent override", () => {
    const state = initialWorkspace();
    expect(resolvePage(state, "", "AAPL")).toBe(state.stockTemplate);
    state.stockOverrides.AAPL = structuredClone(state.stockTemplate);
    state.stockOverrides.AAPL.widgets[0].title = "Apple thesis";
    expect(resolvePage(state, "", "AAPL")!.widgets[0].title).toBe(
      "Apple thesis",
    );
    expect(resolvePage(state, "", "MSFT")!.widgets[0].title).toBe(
      "Price chart",
    );
  });
  it("normalizes tickers and rejects unsafe symbols", () => {
    expect(symbolSchema.parse(" brk-b ")).toBe("BRK-B");
    for (const symbol of [
      "../foo",
      "AAPL&mode=demo",
      "<script>",
      "",
      "A".repeat(16),
    ])
      expect(symbolSchema.safeParse(symbol).success).toBe(false);
  });
  it("keeps widget and watchlist counts bounded", () => {
    let page = homePage();
    for (let i = 0; i < 40; i++) page = addWidget(page, "notes");
    expect(page.widgets).toHaveLength(30);
    const state = initialWorkspace();
    state.watchlists[0].symbols = Array(31).fill("AAPL");
    expect(workspaceSchema.safeParse(state).success).toBe(false);
  });
});
