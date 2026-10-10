import { expect, test, type Page } from "@playwright/test";

async function readState(page: Page) {
  return page.evaluate(() =>
    JSON.parse(localStorage.getItem("folio.workspace.v1") || "null"),
  );
}
async function openSettings(page: Page) {
  await page.getByRole("button", { name: "Settings & preferences" }).click();
}
test.beforeEach(async ({ page }) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Overview", exact: true }),
  ).toBeVisible();
});

test("renders the five-section workspace with labeled demo prices", async ({
  page,
}) => {
  await expect(page.locator(".widget-card")).toHaveCount(5);
  await expect(page.getByTestId("widget-market")).toContainText("SPY");
  await expect(page.getByTestId("widget-market")).toContainText("$588.24");
  await expect(page.getByTestId("widget-watchlist")).toContainText("$227.52");
  await expect(
    page.getByTestId("widget-watchlist").getByText("DEMO").first(),
  ).toBeVisible();
  await expect(page.getByTestId("widget-earnings")).toContainText(
    "Not an actual earnings schedule",
  );
  await page.screenshot({
    path: "test-results/overview-desktop.png",
    fullPage: true,
  });
});

test("adds, configures, persists, and removes a widget", async ({ page }) => {
  await page.getByRole("button", { name: "Add widget", exact: true }).click();
  await page.getByRole("button", { name: /Research notes/ }).click();
  await page
    .getByRole("textbox", { name: "Research notes" })
    .fill("Watch operating margins next quarter.");
  await page
    .getByRole("button", { name: "Settings for Research notes" })
    .click();
  await page.getByLabel("Title", { exact: true }).fill("Investment thesis");
  await page.getByLabel("Width", { exact: true }).fill("8");
  await page.getByRole("button", { name: "Save settings" }).click();
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Investment thesis" }),
  ).toBeVisible();
  await expect(
    page.getByRole("textbox", { name: "Research notes" }),
  ).toHaveValue("Watch operating margins next quarter.");
  expect((await readState(page)).pages[0].layout.at(-1).w).toBe(8);
  await page.getByRole("button", { name: "Edit layout" }).click();
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Remove Investment thesis" }).click();
  await expect(
    page.getByRole("heading", { name: "Investment thesis" }),
  ).toHaveCount(0);
  await expect(page.locator(".widget-card")).toHaveCount(5);
});

test("dragging and resizing saves grid geometry across reloads", async ({
  page,
}) => {
  await page.getByRole("button", { name: "Edit layout" }).click();
  const widget = page.getByTestId("widget-market");
  const handle = await widget.locator(".widget-drag-handle").boundingBox();
  if (!handle) throw new Error("Drag handle missing");
  const destination = await page
    .getByTestId("widget-movers")
    .locator(".widget-drag-handle")
    .boundingBox();
  if (!destination) throw new Error("Destination missing");
  await page.mouse.move(handle.x + 45, handle.y + handle.height / 2);
  await page.mouse.down();
  await page.mouse.move(
    destination.x + 45,
    destination.y + destination.height / 2,
    { steps: 12 },
  );
  await page.mouse.up();
  await expect
    .poll(
      async () =>
        (await readState(page))?.pages[0].layout.find(
          (l: { i: string }) => l.i === "market",
        ).x,
    )
    .toBe(9);
  await widget.locator(".react-resizable-handle").scrollIntoViewIfNeeded();
  const resize = await widget.locator(".react-resizable-handle").boundingBox();
  if (!resize) throw new Error("Resize handle missing");
  await page.mouse.move(
    resize.x + resize.width / 2,
    resize.y + resize.height / 2,
  );
  await page.mouse.down();
  await page.mouse.move(
    resize.x + resize.width / 2,
    resize.y + resize.height / 2 + 100,
    { steps: 10 },
  );
  await page.mouse.up();
  await expect
    .poll(
      async () =>
        (await readState(page)).pages[0].layout.find(
          (l: { i: string }) => l.i === "market",
        ).h,
    )
    .toBeGreaterThan(7);
  const layout = (await readState(page)).pages[0].layout;
  await page.reload();
  await expect(page.getByTestId("widget-market")).toBeVisible();
  expect((await readState(page)).pages[0].layout).toEqual(layout);
});

test("creates and renames a custom dashboard", async ({ page }) => {
  await page
    .getByRole("button", { name: "New dashboard", exact: true })
    .click();
  await page.getByLabel("Name", { exact: true }).fill("Long-term ideas");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page).toHaveURL(/dashboard/);
  await page.getByRole("button", { name: "Add your first widget" }).click();
  await page.getByRole("button", { name: /Price chart/ }).click();
  await page.getByRole("button", { name: "Rename", exact: true }).click();
  await page.getByLabel("Name", { exact: true }).fill("Quality compounders");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Quality compounders" }),
  ).toBeVisible();
  await expect(page.locator(".widget-card")).toHaveCount(1);
});

test("search, stock overrides, chart ranges, and ticker-specific notes", async ({
  page,
}) => {
  await page.keyboard.press("/");
  await page.getByRole("textbox", { name: "Search stocks" }).fill("AAPL");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: /AAPL Apple/ })
    .click();
  await expect(page).toHaveURL(/stock\/AAPL/);
  await page
    .getByRole("textbox", { name: "Research notes" })
    .fill("Apple-only thesis");
  await page.getByRole("button", { name: "Customize only this stock" }).click();
  await page.getByRole("button", { name: "Settings for Price chart" }).click();
  await page.getByLabel("Title", { exact: true }).fill("Apple price history");
  await page.getByRole("button", { name: "Save settings" }).click();
  await page.getByRole("button", { name: "1Y", exact: true }).click();
  await page
    .getByRole("navigation", { name: "Quick access stocks" })
    .getByRole("button", { name: /MSFT/ })
    .click();
  await expect(
    page.getByRole("heading", { name: "Price chart", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("textbox", { name: "Research notes" }),
  ).toHaveValue("");
  await page.goto("/stock/AAPL");
  await expect(
    page.getByRole("heading", { name: "Apple price history" }),
  ).toBeVisible();
  await expect(
    page.getByRole("textbox", { name: "Research notes" }),
  ).toHaveValue("Apple-only thesis");
  await expect(
    page.getByRole("button", { name: "1Y", exact: true }),
  ).toHaveClass("active");
});

test("manages multiple watchlists and validates duplicate symbols", async ({
  page,
}) => {
  await openSettings(page);
  await page.getByLabel("New watchlist name").fill("New ideas");
  await page.getByRole("button", { name: "Create", exact: true }).click();
  await page.getByRole("button", { name: "Close dialog" }).click();
  await page
    .getByLabel("Selected watchlist")
    .selectOption({ label: "New ideas" });
  await page.getByLabel("Add ticker to watchlist").fill("nflx");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.getByTestId("widget-watchlist")).toContainText("NFLX");
  await page.getByLabel("Add ticker to watchlist").fill("NFLX");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expect(
    page.getByRole("alert").filter({ hasText: "already in your watchlist" }),
  ).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("Selected watchlist")).toHaveValue(
    (await readState(page)).watchlists.at(-1).id,
  );
  await page.getByRole("button", { name: "Remove NFLX" }).click();
  await expect(page.getByTestId("widget-watchlist")).toContainText(
    "Your next idea starts here",
  );
});

test("exports and imports a workspace, rejecting invalid imports", async ({
  page,
}) => {
  await openSettings(page);
  await page.getByLabel("Appearance").selectOption("light");
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export workspace" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("folio-workspace.json");
  const state = await readState(page);
  state.pages[0].name = "Imported workspace";
  await page.getByLabel("Import workspace file").setInputFiles({
    name: "invalid.json",
    mimeType: "application/json",
    buffer: Buffer.from('{"version":99}'),
  });
  await expect(page.getByRole("dialog").getByRole("alert")).toContainText(
    "Invalid workspace",
  );
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByLabel("Import workspace file").setInputFiles({
    name: "workspace.json",
    mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify(state)),
  });
  await expect(
    page.getByRole("heading", { name: "Imported workspace" }),
  ).toBeVisible();
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await expect(
    page.getByRole("heading", { name: "Imported workspace" }),
  ).toBeVisible();
});

test("mobile layout stays within the viewport without overwriting desktop geometry", async ({
  page,
}) => {
  await page.getByRole("button", { name: "Switch to light theme" }).click();
  const layout = (await readState(page)).pages[0].layout;
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    page.getByRole("button", { name: "Open navigation" }),
  ).toBeVisible();
  await expect(page.getByTestId("widget-watchlist")).toContainText("$227.52");
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    )
    .toBe(true);
  const mobileBoxes = await page.locator(".widget-card").evaluateAll((cards) =>
    cards
      .map((card) => {
        const r = card.getBoundingClientRect();
        return { top: r.top, bottom: r.bottom };
      })
      .sort((a, b) => a.top - b.top),
  );
  for (let i = 1; i < mobileBoxes.length; i++)
    expect(mobileBoxes[i].top).toBeGreaterThanOrEqual(
      mobileBoxes[i - 1].bottom,
    );
  expect((await readState(page)).pages[0].layout).toEqual(layout);
  await page.screenshot({
    path: "test-results/overview-mobile.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("button", { name: "Settings & preferences" }).click();
  await expect(
    page.getByRole("dialog", { name: "Workspace settings" }),
  ).toBeVisible();
});

test("unknown tickers show unavailable states instead of invented quotes", async ({
  page,
}) => {
  await page.goto("/stock/UNKNOWN");
  await expect(page.getByTestId("widget-stock-quote")).toContainText(
    "Data is unavailable for UNKNOWN",
  );
  await expect(
    page
      .getByTestId("widget-stock-quote")
      .getByRole("button", { name: "Retry" }),
  ).toBeVisible();
});

test("broken saved data recovers to defaults with a visible warning", async ({
  page,
}) => {
  await page.evaluate(() =>
    localStorage.setItem("folio.workspace.v1", "broken"),
  );
  await page.reload();
  await expect(
    page.getByRole("alert").filter({ hasText: "could not be read" }),
  ).toBeVisible();
  await expect(page.locator(".widget-card")).toHaveCount(5);
});

test("API rejects malformed requests and returns source metadata", async ({
  request,
}) => {
  expect(
    (await request.get("/api/market?symbol=AAPL&range=invalid")).status(),
  ).toBe(400);
  const response = await request.get("/api/market?symbol=AAPL&mode=demo");
  expect(response.ok()).toBeTruthy();
  expect((await response.json()).source.demo).toBe(true);
});

test("market symbols and calendar dates are configurable and persistent", async ({
  page,
}) => {
  await page
    .getByRole("button", { name: "Settings for Market overview" })
    .click();
  await page
    .getByRole("dialog")
    .getByRole("combobox", { name: "Watchlist", exact: true })
    .selectOption("core");
  await page.getByRole("button", { name: "Save settings" }).click();
  await expect(page.getByTestId("widget-market")).toContainText("AAPL");
  await page.getByLabel("Earnings date").fill("2026-10-15");
  await page.reload();
  await expect(page.getByLabel("Earnings date")).toHaveValue("2026-10-15");
  await expect(page.getByTestId("widget-market")).toContainText("AAPL");
  await page.getByRole("button", { name: "Follow today" }).click();
  expect(
    (await readState(page)).pages[0].widgets.find(
      (w: { id: string }) => w.id === "earnings",
    ).earningsDate,
  ).toBe("");
});

test("shared stock template edits carry across tickers", async ({ page }) => {
  await page.goto("/stock/AAPL");
  await page.getByRole("button", { name: "Settings for Price chart" }).click();
  await page.getByLabel("Title", { exact: true }).fill("Long-term performance");
  await page.getByRole("button", { name: "Save settings" }).click();
  await page.goto("/stock/MSFT");
  await expect(
    page.getByRole("heading", { name: "Long-term performance" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Settings for Long-term performance" })
    .click();
  await page.getByLabel("Default ticker").fill("SPY");
  await page.getByLabel("Follow the page ticker on stock pages").uncheck();
  await page.getByRole("button", { name: "Save settings" }).click();
  await expect(page.getByTestId("widget-stock-chart")).toContainText("$588.24");
  await expect(page.getByTestId("widget-stock-quote")).toContainText("$428.76");
});

test("deleting a referenced watchlist repairs widgets and leaves exports valid", async ({
  page,
}) => {
  await openSettings(page);
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Delete Core watchlist" }).click();
  await page.getByRole("button", { name: "Close dialog" }).click();
  await expect(page.getByLabel("Selected watchlist")).toHaveValue("benchmarks");
  await page.getByRole("button", { name: "Edit layout" }).click();
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Reset", exact: true }).click();
  await page.reload();
  await expect(
    page.getByRole("alert").filter({ hasText: "could not be read" }),
  ).toHaveCount(0);
  await expect(page.getByTestId("widget-market")).toContainText("SPY");
  await expect(page.getByLabel("Selected watchlist")).toHaveValue("benchmarks");
});

test("storage failures are visible and changes remain usable in memory", async ({
  page,
}) => {
  await page.evaluate(() => {
    Storage.prototype.setItem = () => {
      throw new DOMException("Quota exceeded", "QuotaExceededError");
    };
  });
  await page.getByRole("button", { name: "Switch to light theme" }).click();
  await expect(
    page
      .getByRole("alert")
      .filter({ hasText: "storage is unavailable or full" }),
  ).toBeVisible();
  await expect(page.getByText("Storage needs attention")).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
});
