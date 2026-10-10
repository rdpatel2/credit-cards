import { expect, test, type Page } from "@playwright/test";

async function recordTrade(
  page: Page,
  side: string,
  quantity: string,
  price: string,
  fees = "0",
) {
  const editor = page.getByRole("region", { name: "NFLX · Cost basis" });
  await editor.getByLabel("Action", { exact: true }).selectOption(side);
  await editor.getByLabel("Trade date", { exact: true }).fill("2025-01-02");
  await editor.getByLabel("Shares", { exact: true }).fill(quantity);
  await editor.getByLabel("Price per share (USD)", { exact: true }).fill(price);
  await editor.getByLabel("Trade fees (USD)", { exact: true }).fill(fees);
  await editor.getByRole("button", { name: "Add trade", exact: true }).click();
}

test("home widget opens an editor that saves purchases, fees, sales, and edited cost basis", async ({
  page,
}) => {
  await page.goto("/");
  await page
    .getByTestId("widget-watchlist")
    .getByRole("link", { name: "Watchlist", exact: true })
    .click();
  await expect(page).toHaveURL(/\/watchlist\/core$/);
  await page.getByLabel("Add ticker to watchlist").fill("nflx");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  const editor = page.getByRole("region", { name: "NFLX · Cost basis" });
  await expect(editor).toBeVisible();
  await recordTrade(page, "buy", "10", "100", "10");
  await recordTrade(page, "buy", "10", "200");
  const summary = editor.locator(".portfolio-summary");
  await expect(summary).toContainText("$3,010.00");
  await expect(summary).toContainText("$150.50");
  await recordTrade(page, "sell", "5", "250");
  await expect(summary).toContainText("$2,505.00");
  await expect(summary).toContainText("$167.00");
  await editor
    .getByRole("button", { name: "Edit trade 2", exact: true })
    .click();
  await editor.getByLabel("Price per share (USD)", { exact: true }).fill("300");
  await editor.getByRole("button", { name: "Save trade changes" }).click();
  await expect(summary).toContainText("$3,505.00");
  await expect(summary).toContainText("$233.67");
  await page
    .getByLabel("Watchlist name", { exact: true })
    .fill("Long-term ideas");
  await page.getByRole("button", { name: "Save name" }).click();
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Long-term ideas", exact: true }),
  ).toBeVisible();
  const row = page
    .locator(".watchlist-editor-content > .table-scroll > table tbody tr")
    .filter({ hasText: "NFLX" });
  await expect(row).toContainText("$3,505.00");
  await expect(row).toContainText("$233.67");
  await page.getByRole("button", { name: "Edit cost basis for NFLX" }).click();
  await expect(editor.locator(".ledger-table tbody tr")).toHaveCount(3);
  await page.getByRole("link", { name: "Overview", exact: true }).click();
  await page
    .getByRole("button", { name: "Show positions", exact: true })
    .click();
  await expect(page.getByTestId("widget-watchlist")).toContainText("$233.67");
});

test("widget links follow the selected list and ticker validation and removal preserve history", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByLabel("Selected watchlist").selectOption("benchmarks");
  await page.getByRole("link", { name: "Edit watchlist", exact: true }).click();
  await expect(page).toHaveURL(/\/watchlist\/benchmarks$/);
  await page.getByLabel("Selected watchlist").selectOption("core");
  await expect(page).toHaveURL(/\/watchlist\/core$/);
  await page.getByLabel("Add ticker to watchlist").fill("???");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expect(
    page
      .getByRole("region", { name: "Edit watchlist", exact: true })
      .getByRole("alert"),
  ).toContainText("valid ticker");
  await page.getByLabel("Add ticker to watchlist").fill("aapl");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expect(
    page
      .getByRole("region", { name: "Edit watchlist", exact: true })
      .getByRole("alert"),
  ).toContainText("already in your watchlist");
  await page.getByLabel("Add ticker to watchlist").fill("NFLX");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await recordTrade(page, "buy", "0.5", "100", "1");
  await page.getByRole("button", { name: "Remove NFLX", exact: true }).click();
  await expect(
    page.getByRole("region", { name: "NFLX · Cost basis" }),
  ).toHaveCount(0);
  await page.getByLabel("Add ticker to watchlist").fill("NFLX");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.locator(".portfolio-summary")).toContainText("$51.00");
  await expect(page.locator(".ledger-table tbody tr")).toHaveCount(1);
});

test("mobile editor works without quotes and an empty list can accept stocks", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route("**/api/market?symbol=*", (route) =>
    route.fulfill({ status: 503, json: { error: "Offline" } }),
  );
  await page.goto("/watchlist/benchmarks");
  const remove = page.getByRole("button", { name: /^Remove / });
  while (await remove.count()) await remove.first().click();
  await expect(
    page.getByText("Your next idea starts here.", { exact: false }),
  ).toBeVisible();
  await page.getByLabel("Add ticker to watchlist").fill("NFLX");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await recordTrade(page, "buy", "0.25", "100");
  await expect(page.locator(".portfolio-summary")).toContainText("$25.00");
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    )
    .toBe(true);
  await page.screenshot({
    path: "test-results/watchlist-editor-mobile.png",
    fullPage: true,
  });
  await page.reload();
  await page.getByRole("button", { name: "Edit cost basis for NFLX" }).click();
  await expect(page.locator(".portfolio-summary")).toContainText("$25.00");
});

test("unknown watchlist routes show the workspace recovery page", async ({
  page,
}) => {
  await page.goto("/watchlist/missing");
  await expect(
    page.getByRole("heading", { name: "This page isn’t in your workspace" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Go to overview" }).click();
  await expect(page.getByTestId("widget-watchlist")).toBeVisible();
});
