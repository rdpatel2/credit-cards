import { expect, test, type Page } from "@playwright/test";
import {
  addWidget,
  initialWorkspace,
  parseWorkspace,
  type Workspace,
} from "../src/lib/workspace";

async function seed(page: Page, state: Workspace) {
  await page.addInitScript((state) => {
    if (!localStorage.getItem("folio.workspace.v1"))
      localStorage.setItem("folio.workspace.v1", JSON.stringify(state));
  }, state);
}

test("My trades shows only submitted records independently of watchlists and persists edits", async ({
  page,
}) => {
  const state = initialWorkspace();
  state.dataMode = "demo";
  state.watchlists[1].symbols.push("AAPL");
  state.trades = {
    AAPL: [
      {
        id: "buy",
        side: "buy",
        date: "2025-01-01",
        quantity: 10,
        price: 100,
        fees: 5,
      },
      {
        id: "sell",
        side: "sell",
        date: "2025-01-02",
        quantity: 2,
        price: 200,
        fees: 1,
      },
    ],
    NFLX: [
      {
        id: "buy",
        side: "buy",
        date: "2025-01-03",
        quantity: 0.5,
        price: 500,
        fees: 2,
      },
    ],
  };
  await seed(page, state);
  await page.goto("/");
  await page.getByRole("button", { name: "Add widget", exact: true }).click();
  await page.getByRole("button", { name: /My trades/ }).click();
  await page.getByRole("button", { name: "Done editing", exact: true }).click();
  const widget = page.getByRole("region", { name: "My trades", exact: true });
  const rows = widget.locator("tbody tr");
  await expect(rows).toHaveCount(3);
  await expect(rows.nth(0)).toContainText("NFLX");
  await expect(rows.nth(0)).toContainText("$252.00");
  await expect(rows.nth(1)).toContainText("Sell");
  await expect(rows.nth(1)).toContainText("$399.00");
  await expect(rows.nth(2)).toContainText("$1,005.00");
  await expect(widget).not.toContainText("MSFT");
  await expect(widget).not.toContainText("DEMO");
  await page.getByRole("button", { name: "Remove AAPL", exact: true }).click();
  await expect(rows).toHaveCount(3);
  await widget.getByLabel("Filter trades by action").selectOption("sell");
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("$399.00");
  await widget.getByLabel("Filter trades by symbol").selectOption("NFLX");
  await expect(widget).toContainText("No trades match these filters.");
  await widget.getByLabel("Filter trades by action").selectOption("");
  await widget.getByRole("button", { name: "Manage NFLX trades" }).click();
  const dialog = page.getByRole("dialog", { name: "NFLX · Trades & taxes" });
  await dialog
    .getByRole("button", { name: "Edit trade 1", exact: true })
    .click();
  await dialog.getByLabel("Price per share (USD)", { exact: true }).fill("300");
  await dialog.getByRole("button", { name: "Save trade changes" }).click();
  await dialog.getByRole("button", { name: "Close dialog" }).click();
  await expect(rows.first()).toContainText("$152.00");
  await page.reload();
  await expect(rows).toHaveCount(3);
  await expect(rows.first()).toContainText("$152.00");
  const raw = await page.evaluate(() =>
    localStorage.getItem("folio.workspace.v1")!,
  );
  expect(parseWorkspace(raw).pages[0].widgets.at(-1)?.type).toBe("trades");
  await widget.getByRole("button", { name: "Manage NFLX trades" }).click();
  page.once("dialog", (prompt) => prompt.accept());
  await dialog
    .getByRole("button", { name: "Delete trade 1", exact: true })
    .click();
  await dialog.getByRole("button", { name: "Close dialog" }).click();
  await expect(rows).toHaveCount(2);
  await expect(
    widget.getByLabel("Filter trades by symbol").locator("option"),
  ).toHaveCount(2);
});

test("empty My trades fills immediately after a trade is recorded", async ({
  page,
}) => {
  const state = initialWorkspace();
  state.pages[0] = addWidget(state.pages[0], "trades");
  await seed(page, state);
  await page.goto("/");
  const widget = page.getByRole("region", { name: "My trades", exact: true });
  await expect(widget).toContainText("No trades recorded yet.");
  await expect(
    widget.getByRole("link", { name: "Open watchlist editor" }),
  ).toHaveAttribute("href", "/watchlist/core");
  await page
    .getByRole("button", { name: "Trades for AAPL", exact: true })
    .click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Trade date", { exact: true }).fill("2025-01-02");
  await dialog.getByLabel("Shares", { exact: true }).fill("0.25");
  await dialog.getByLabel("Price per share (USD)", { exact: true }).fill("100");
  await dialog.getByRole("button", { name: "Add trade", exact: true }).click();
  await dialog.getByRole("button", { name: "Close dialog" }).click();
  await expect(widget.locator("tbody tr")).toHaveCount(1);
  await expect(widget).toContainText("$25.00");
});

test("mobile trade history paginates all submitted records", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const state = initialWorkspace();
  state.pages[0] = addWidget(state.pages[0], "trades");
  state.trades.AAPL = Array.from({ length: 21 }, (_, index) => ({
    id: `trade-${index}`,
    side: "buy",
    date: "2025-01-01",
    quantity: index + 1,
    price: 100,
    fees: 0,
  }));
  await seed(page, state);
  await page.goto("/");
  const widget = page.getByRole("region", { name: "My trades", exact: true });
  await expect(widget.locator("tbody tr")).toHaveCount(20);
  await expect(widget.locator("tbody tr").first()).toContainText("$2,100.00");
  await widget.getByRole("button", { name: "Next trades" }).click();
  await expect(widget.locator("tbody tr")).toHaveCount(1);
  await expect(widget.locator("tbody tr")).toContainText("$100.00");
  await expect(
    widget.getByRole("button", { name: "Next trades" }),
  ).toBeDisabled();
  await widget.getByRole("button", { name: "Previous trades" }).click();
  await expect(widget.locator("tbody tr")).toHaveCount(20);
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    )
    .toBe(true);
});
