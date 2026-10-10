import { test, expect, type Page } from "@playwright/test";
import { initialWorkspace } from "../src/lib/workspace";
import { readFile } from "node:fs/promises";

async function openTrades(page: Page) {
  await page.goto("/");
  await page
    .getByRole("button", { name: "Trades for AAPL", exact: true })
    .click();
  return page.getByRole("dialog", { name: "AAPL · Trades & taxes" });
}
async function addTrade(
  page: Page,
  side: "buy" | "sell",
  date: string,
  quantity: string,
  price: string,
) {
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Action", { exact: true }).selectOption(side);
  await dialog.getByLabel("Trade date", { exact: true }).fill(date);
  await dialog.getByLabel("Shares", { exact: true }).fill(quantity);
  await dialog.getByLabel("Price per share (USD)", { exact: true }).fill(price);
  await dialog.getByRole("button", { name: "Add trade", exact: true }).click();
}
test("buys and sells persist with FIFO average cost, invalid edits and deletions rejected", async ({
  page,
}) => {
  const dialog = await openTrades(page);
  await addTrade(page, "buy", "2024-01-02", "10", "100");
  await addTrade(page, "buy", "2025-01-02", "10", "200");
  await addTrade(page, "sell", "2026-01-03", "15", "300");
  await expect(dialog.locator(".portfolio-summary").first()).toContainText(
    "$200.00",
  );
  await expect(dialog.locator(".portfolio-summary").first()).toContainText(
    "$2,500.00",
  );
  await addTrade(page, "sell", "2026-01-04", "6", "300");
  await expect(dialog.getByRole("alert")).toContainText("exceeds shares");
  await expect(dialog.locator(".ledger-table tbody tr")).toHaveCount(3);
  await dialog
    .getByRole("button", { name: "Edit trade 1", exact: true })
    .click();
  await dialog.getByLabel("Shares", { exact: true }).fill("1");
  await dialog.getByRole("button", { name: "Save trade changes" }).click();
  await expect(dialog.getByRole("alert")).toContainText("exceeds shares");
  await dialog.getByRole("button", { name: "Cancel edit" }).click();
  page.on("dialog", (d) => d.accept());
  await dialog
    .getByRole("button", { name: "Delete trade 1", exact: true })
    .click();
  await expect(dialog.getByRole("alert")).toContainText("exceeds shares");
  await dialog.getByRole("button", { name: "Close dialog" }).click();
  await page.getByRole("button", { name: "Show positions" }).click();
  await page.reload();
  await expect(
    page.getByTestId("widget-watchlist").getByText("$200.00", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Trades for AAPL", exact: true })
    .click();
  await expect(dialog.locator(".ledger-table tbody tr")).toHaveCount(3);
  await dialog
    .getByRole("button", { name: "Delete trade 3", exact: true })
    .click();
  await expect(dialog.locator(".portfolio-summary").first()).toContainText(
    "$150.00",
  );
});

test("sale scenario uses income-based federal and NC tax without using demo prices", async ({
  page,
}) => {
  const dialog = await openTrades(page);
  await addTrade(page, "buy", "2024-01-02", "10", "100");
  await dialog
    .getByLabel("Ordinary income before deductions", { exact: true })
    .fill("100000");
  await dialog
    .getByLabel("Net investment income before this sale", { exact: true })
    .fill("0");
  await dialog.getByRole("button", { name: "Save tax assumptions" }).click();
  await expect(dialog.getByLabel("Hypothetical sale price (USD)")).toHaveValue(
    "",
  );
  await dialog.getByLabel("Sale date", { exact: true }).fill("2026-09-30");
  await dialog.getByLabel("Shares to sell", { exact: true }).fill("10");
  await dialog.getByLabel("Hypothetical sale price (USD)").fill("200");
  await dialog
    .getByRole("button", { name: "Estimate sale", exact: true })
    .click();
  await expect(dialog.locator(".tax-result")).toContainText("$189.90");
  await expect(dialog.locator(".tax-result")).toContainText("$1,810.10");
  await page.screenshot({
    path: "test-results/trades-tax-desktop.png",
    fullPage: true,
  });
  await dialog.getByLabel("Hypothetical sale price (USD)").fill("50");
  await dialog
    .getByRole("button", { name: "Estimate sale", exact: true })
    .click();
  await expect(dialog.getByText(/Tax estimate withheld/)).toBeVisible();
  await expect(dialog.locator(".tax-result")).toHaveCount(0);
  await dialog.getByLabel("Hypothetical sale price (USD)").fill("200");
  await dialog.getByLabel("Sale date", { exact: true }).fill("2027-09-30");
  await dialog
    .getByRole("button", { name: "Estimate sale", exact: true })
    .click();
  await expect(
    dialog.getByText(/Tax tables currently support 2026 only/),
  ).toBeVisible();
  await page.reload();
  await page
    .getByRole("button", { name: "Trades for AAPL", exact: true })
    .click();
  await dialog.getByText("Edit tax assumptions", { exact: false }).click();
  await expect(
    dialog.getByLabel("Ordinary income before deductions", { exact: true }),
  ).toHaveValue("100000");
});

test("trade controls stay accessible on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const dialog = await openTrades(page);
  await addTrade(page, "buy", "2025-01-02", "0.25", "100");
  await expect(dialog.locator(".portfolio-summary").first()).toContainText(
    "0.25",
  );
  await expect
    .poll(() => dialog.evaluate((el) => el.scrollWidth <= el.clientWidth))
    .toBe(true);
  await page.screenshot({
    path: "test-results/trades-mobile.png",
    fullPage: true,
  });
});

test("analyst widget follows the stock and persists selectable watchlist proxy", async ({
  page,
}) => {
  const state = initialWorkspace();
  state.dataMode = "demo";
  await page.addInitScript((state) => {
    if (!localStorage.getItem("folio.workspace.v1"))
      localStorage.setItem("folio.workspace.v1", JSON.stringify(state));
  }, state);
  await page.goto("/stock/AAPL");
  const widget = page.getByTestId("widget-stock-analyst");
  await expect(widget.locator(".consensus-heading")).toContainText("Bullish");
  await expect(widget).toContainText("DEMO");
  await expect(widget.getByRole("meter")).toHaveCount(5);
  await widget.getByLabel("Analyst coverage").selectOption("watchlist");
  await expect(widget).toContainText("8 / 8 stocks covered");
  await widget.getByLabel("Analyst watchlist").selectOption("benchmarks");
  await expect(widget).toContainText("No coverage");
  await page.reload();
  await expect(widget.getByLabel("Analyst watchlist")).toHaveValue(
    "benchmarks",
  );
  await widget.getByLabel("Analyst watchlist").selectOption("core");
  await page.screenshot({
    path: "test-results/analyst-consensus.png",
    fullPage: true,
  });
});

test("live aggregate excludes demo fallbacks and compares only matched stocks", async ({
  page,
}) => {
  await page.route("**/api/market?kind=analyst*", async (route) => {
    const symbol = new URL(route.request().url()).searchParams.get("symbol");
    const covered = symbol === "AAPL";
    await route.fulfill({
      json: {
        data: {
          symbol,
          periods: [
            {
              period: "0m",
              counts: covered ? [0, 0, 0, 0, 10] : [10, 0, 0, 0, 0],
            },
            { period: "-1m", counts: [0, 0, 0, 10, 0] },
          ],
        },
        source: {
          demo: !covered,
          provider: covered ? "Yahoo" : "Demo",
          asOf: "2026-09-30T12:00:00Z",
        },
      },
    });
  });
  await page.goto("/stock/AAPL");
  const widget = page.getByTestId("widget-stock-analyst");
  await widget.getByLabel("Analyst coverage").selectOption("watchlist");
  await expect(widget).toContainText("1 / 8 stocks covered");
  await expect(widget.locator(".consensus-heading")).toContainText(
    "Very bullish",
  );
  await expect(widget).toContainText(
    "+1.00 points vs prior month (1 matched stocks)",
  );
});

test("offline trade records survive ticker removal and workspace export/import", async ({
  page,
}) => {
  await page.route("**/api/market?symbol=AAPL*", (route) =>
    route.fulfill({ status: 503, json: { error: "Offline" } }),
  );
  const dialog = await openTrades(page);
  await addTrade(page, "buy", "2025-01-02", "2.5", "100");
  await expect(dialog.locator(".portfolio-summary").first()).toContainText(
    "$250.00",
  );
  await dialog.getByRole("button", { name: "Close dialog" }).click();
  const widget = page.getByTestId("widget-watchlist");
  await widget
    .getByRole("button", { name: "Remove AAPL", exact: true })
    .click();
  await widget.getByLabel("Add ticker").fill("AAPL");
  await widget.getByRole("button", { name: "Add", exact: true }).click();
  await page
    .getByRole("button", { name: "Trades for AAPL", exact: true })
    .click();
  await expect(dialog.locator(".ledger-table tbody tr")).toHaveCount(1);
  await dialog.getByRole("button", { name: "Close dialog" }).click();
  await page.getByRole("button", { name: "Settings & preferences" }).click();
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export workspace" }).click();
  const downloaded = await downloadPromise;
  const raw = await readFile((await downloaded.path())!, "utf8");
  expect(JSON.parse(raw).trades.AAPL[0].quantity).toBe(2.5);
  page.once("dialog", (d) => d.accept());
  await page.getByLabel("Import workspace file").setInputFiles({
    name: "portfolio.json",
    mimeType: "application/json",
    buffer: Buffer.from(raw),
  });
  await page.reload();
  await page
    .getByRole("button", { name: "Trades for AAPL", exact: true })
    .click();
  await expect(dialog.locator(".ledger-table tbody tr")).toHaveCount(1);
  await expect(dialog.locator(".portfolio-summary").first()).toContainText(
    "$250.00",
  );
});
