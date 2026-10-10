import { test, expect, type Page } from "@playwright/test";
import type { LayoutItem } from "../src/lib/workspace";
async function stored(page: Page) {
  return page.evaluate(() =>
    JSON.parse(localStorage.getItem("folio.workspace.v1") ?? "null"),
  );
}

test("advanced metrics are available on stock views with persistent per-metric toggles", async ({
  page,
}) => {
  await page.goto("/stock/AAPL");
  const widget = page.getByTestId("widget-stock-metrics");
  await expect(widget.locator('[data-metric="trailingEps"]')).toContainText(
    "$6.08",
  );
  await expect(widget.locator('[data-metric="forwardPE"]')).toContainText(
    "31.87×",
  );
  await page.getByRole("button", { name: "Customize only this stock" }).click();
  await widget.getByRole("button", { name: "Choose metrics" }).click();
  const dialog = page.getByRole("dialog", { name: "Widget settings" });
  await expect(dialog.getByRole("checkbox")).toHaveCount(21);
  await dialog.getByRole("button", { name: "Clear all" }).click();
  await dialog
    .getByRole("checkbox", { name: "EPS (TTM)", exact: true })
    .check();
  await dialog
    .getByRole("checkbox", { name: "Forward P/E", exact: true })
    .check();
  await dialog.getByRole("button", { name: "Save settings" }).click();
  await expect(widget.locator("[data-metric]")).toHaveCount(2);
  await page.reload();
  await expect(widget.locator("[data-metric]")).toHaveCount(2);
  await page.screenshot({
    path: "test-results/stock-advanced-metrics.png",
    fullPage: true,
  });
  await page.goto("/stock/MSFT");
  await expect(
    page.getByTestId("widget-stock-metrics").locator("[data-metric]"),
  ).toHaveCount(8);
});

test("all metric toggles work, including an intentionally empty view", async ({
  page,
}) => {
  await page.goto("/stock/AAPL");
  await page
    .getByRole("button", { name: "Settings for Advanced metrics" })
    .click();
  await page.getByRole("button", { name: "Select all", exact: true }).click();
  await page.getByRole("button", { name: "Save settings" }).click();
  await expect(
    page.getByTestId("widget-stock-metrics").locator("[data-metric]"),
  ).toHaveCount(20);
  await page
    .getByRole("button", { name: "Settings for Advanced metrics" })
    .click();
  await page.getByRole("button", { name: "Clear all", exact: true }).click();
  await page.getByRole("button", { name: "Save settings" }).click();
  await page.reload();
  await expect(page.getByTestId("widget-stock-metrics")).toContainText(
    "No metrics selected",
  );
});

test("market news covers sectors with independent saved views and custom widgets", async ({
  page,
}) => {
  await page.goto("/");
  await page
    .getByRole("navigation", { name: "Dashboards" })
    .getByRole("button", { name: "Market news" })
    .click();
  await expect(
    page.getByRole("heading", { name: "Market news", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".market-article")).toHaveCount(14);
  await page.getByRole("button", { name: "By sector", exact: true }).click();
  await expect(page.locator(".news-sector-group")).toHaveCount(12);
  const sectors = page.getByRole("navigation", { name: "News sectors" });
  await sectors
    .getByRole("button", { name: "Technology", exact: true })
    .click();
  await expect(page).toHaveURL(/news\/technology/);
  await expect(page.locator(".market-article")).toHaveCount(2);
  await page.getByRole("button", { name: "Headlines", exact: true }).click();
  await page.getByRole("button", { name: "Add widget", exact: true }).click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: /Research notes/ })
    .click();
  await page
    .getByRole("textbox", { name: "Research notes" })
    .fill("Focus on chip demand.");
  await sectors.getByRole("button", { name: "Energy", exact: true }).click();
  await expect(page.locator(".market-article")).toHaveCount(1);
  await expect(
    page.getByRole("button", { name: "Cards", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(
    page.getByRole("textbox", { name: "Research notes" }),
  ).toHaveCount(0);
  await sectors
    .getByRole("button", { name: "Technology", exact: true })
    .click();
  await expect(page).toHaveURL(/news\/technology/);
  await page.reload();
  await expect(
    page.getByRole("button", { name: "Headlines", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(
    page.getByRole("textbox", { name: "Research notes" }),
  ).toHaveValue("Focus on chip demand.");
  await page.goto("/news");
  await expect(
    page.getByRole("button", { name: "By sector", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await page.getByLabel("Search market headlines").fill("oil");
  await expect(page.locator(".market-article")).toHaveCount(1);
  await page.getByLabel("Search market headlines").fill("no-matching-headline");
  await expect(
    page.getByText(
      "No matching headlines in the available feeds. Try another sector or search.",
    ),
  ).toBeVisible();
});

test("market news remains readable on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/news");
  await expect(page.locator(".market-article")).toHaveCount(14);
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    )
    .toBe(true);
  await page.screenshot({
    path: "test-results/market-news-mobile.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    )
    .toBe(true);
  await page.screenshot({
    path: "test-results/market-news-desktop.png",
    fullPage: true,
  });
});

test("crossing neighbors during a drag does not shift them, and a drop swaps positions", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Switch to light theme" }).click();
  const before: LayoutItem[] = (await stored(page)).pages[0].layout;
  await page.getByRole("button", { name: "Edit layout" }).click();
  const source = page
    .getByTestId("widget-market")
    .locator(".widget-drag-handle");
  const target = page
    .getByTestId("widget-movers")
    .locator(".widget-drag-handle");
  const a = await source.boundingBox(),
    b = await target.boundingBox();
  if (!a || !b) throw new Error("Grid headers unavailable");
  const neighborBefore = await page
    .getByTestId("widget-watchlist")
    .boundingBox();
  await page.mouse.move(a.x + 40, a.y + a.height / 2);
  await page.mouse.down();
  await page.mouse.move(b.x + 40, b.y + b.height / 2, { steps: 20 });
  expect(await page.getByTestId("widget-watchlist").boundingBox()).toEqual(
    neighborBefore,
  );
  await page.mouse.move(a.x + 40, a.y + a.height / 2, { steps: 20 });
  await page.mouse.up();
  expect((await stored(page)).pages[0].layout).toEqual(before);
  await page.mouse.move(a.x + 40, a.y + a.height / 2);
  await page.mouse.down();
  await page.mouse.move(b.x + 40, b.y + b.height / 2, { steps: 20 });
  await page.mouse.up();
  await expect
    .poll(
      async () =>
        (await stored(page)).pages[0].layout.find(
          (l: LayoutItem) => l.i === "market",
        ).x,
    )
    .toBe(9);
  const after: LayoutItem[] = (await stored(page)).pages[0].layout;
  expect(after.find((l) => l.i === "movers")?.x).toBe(0);
  for (const id of ["watchlist", "news", "earnings"])
    expect(after.find((l) => l.i === id)).toEqual(
      before.find((l) => l.i === id),
    );
  await page.reload();
  await expect(page.getByTestId("widget-market")).toBeVisible();
  expect((await stored(page)).pages[0].layout).toEqual(after);
});

test("old saved workspaces gain the new metrics widget without losing research", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Switch to light theme" }).click();
  await page.evaluate(() => {
    const old = JSON.parse(localStorage.getItem("folio.workspace.v1")!);
    delete old.researchVersion;
    delete old.newsPages;
    old.stockTemplate.widgets = old.stockTemplate.widgets.filter(
      (w: { type: string }) => w.type !== "metrics",
    );
    old.stockTemplate.layout = old.stockTemplate.layout.filter(
      (l: { i: string }) => l.i !== "stock-metrics",
    );
    old.notes["stock-AAPL:stock-notes"] = "Existing research stays here";
    localStorage.setItem("folio.workspace.v1", JSON.stringify(old));
  });
  await page.goto("/stock/AAPL");
  await expect(
    page.getByRole("heading", { name: "Advanced metrics" }),
  ).toBeVisible();
  await expect(
    page.getByRole("textbox", { name: "Research notes" }),
  ).toHaveValue("Existing research stays here");
  await expect(
    page.getByRole("alert").filter({ hasText: "could not be read" }),
  ).toHaveCount(0);
});
