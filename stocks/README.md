# Folio

A free, personal stock research workspace with a modular canvas. Build dashboards from widgets, arrange them on a snapping grid, and customize stock pages with a shared template or ticker-specific overrides.

## Run locally

Requires Node.js 22 or newer and npm. No API keys, subscriptions, database, or login are required.

```sh
npm ci
npm run dev
```

Open http://localhost:3000. For an optimized build:

```sh
npm run build
npm start
```

Optional: copy `.env.example` to `.env.local` and set `MARKET_DATA_MODE=demo` for a fully offline data preview. The UI's **Settings → Market data** also supports demo-only mode. The server flag takes precedence. Assets and fonts are bundled locally.

## What you can do

- Start with five sections: market overview and news on the left, a large watchlist in the center, and movers and earnings on the right.
- Add, remove, rename, drag, and resize any of eleven widgets: watchlist, My trades, chart, statistics, advanced metrics, analyst trend, movers, market overview, news, earnings, and research notes.
- Use **Advanced metrics → Choose metrics** for individual toggles across 20 fields, including EPS, forward EPS, trailing/forward P/E, PEG, price/book, price/sales, market cap, enterprise value, EV/EBITDA, dividend yield, beta, margins, ROE, revenue/earnings growth, revenue, free cash flow, and debt/equity. Select all, clear all, or restore defaults. Missing or non-meaningful values display as `—`.
- Open **Market news** for broad market headlines, with all eleven stock sectors plus a market/economy category. Each sector has its own saved modular layout, article display, and research notes. Choose cards, a headline list, or grouped sector views; search the available headlines. Sector tags are inferred from headline keywords, can overlap, and are not publisher-verified classifications.
- Use widget settings for tickers, ranges, list selection, sorting, columns, row limits, and numeric grid positioning. Numeric positioning supports keyboard access without dragging.
- Create up to 20 named dashboards, with up to 30 widgets each.
- Search popular companies by name or enter any supported US ticker directly. Click a symbol to open its stock page.
- Edit a shared stock template or choose **Customize only this stock**. Notes remain separate for each ticker, even when the layout is shared.
- Maintain up to 20 watchlists, each with up to 30 symbols. Movers rank the selected watchlist, not the whole exchange.
- Click the watchlist widget title or **Edit watchlist** to open `/watchlist/<id>` (also available from **Watchlists** in the sidebar). Add/remove stocks, rename or switch lists, and select **Edit cost basis** to record purchases and sales directly on the page. New tickers open the cost-basis form automatically. The table shows shares, average cost, and remaining cost basis, including fees; changes persist locally and share the existing trade history.
- Add **My trades** through **Add widget** for a ledger of submitted purchases and sales across all symbols, including stocks removed from a watchlist. Filter by symbol or buy/sell, browse history newest first, and click a ticker to manage its records. This widget contains only your saved trades.
- Open **Trades** beside a watchlist ticker to record purchases and sales (date, fractional shares, execution price, fees), edit/delete records, and simulate a FIFO sale. **Show positions** displays remaining shares and average cost directly in the watchlist.
- Use **Analyst trend** on stock pages or add it to any dashboard. Switch to **Market proxy (watchlist)** and select a watchlist to aggregate covered stocks. The widget shows five sentiment levels, recommendation counts, coverage, and a matched-stock prior-month comparison.
- Switch between dark and light themes. On mobile, widgets stack without changing saved desktop geometry.
- Export and import all layouts, watchlists, preferences, notes, trades, and tax assumptions as validated JSON. Exports contain sensitive financial information in plain text; store them securely.

Dragging preserves the layout from the start of the gesture. Dropping onto another widget's origin swaps positions when both fit; partial overlaps resolve to the nearest free position or minimally nudge the collided widget. Unaffected widgets stay in place and widget sizes are preserved during moves. Resizing and numeric positioning use the same collision logic. Passing across another widget and returning to the original position no longer pushes its neighbors down the page.

Existing version 1 workspaces upgrade automatically: advanced metrics are added once to stock templates and overrides without changing existing geometry or notes. A stock view already at the 30-widget limit keeps its current widgets; remove one and add Advanced metrics manually. Deliberately removed widgets and empty metric selections are preserved after the upgrade.

Edits save automatically to local storage. Export a backup before clearing browser data or moving to another device. The app reports storage failures instead of claiming changes were saved. No account sync or collaborative editing is included.

## Positions and potential sale taxes

The ledger represents **one personal USD taxable account**, shared across watchlists. Repeating a ticker in multiple watchlists never duplicates holdings. Removing a ticker retains its history; add it again to view those trades. There is no broker connection or trading capability.

Purchases add price × shares plus fees to basis. Sales consume the oldest lots first (FIFO), allocate sale fees proportionally, and reject sales exceeding holdings at that date. Same-day trades follow entry order. Invalid edits/deletions are rejected if they would break later sales. Decimal arithmetic preserves fractional-share accounting. The dialog distinguishes the all-purchase weighted average from the average basis of shares still held; US stock-sale gains use lot basis, **not average cost**.

**What if I sell?** estimates an incremental 2026 tax increase for a full-year North Carolina resident. Enter ordinary income before deductions, existing net long-term gains/qualified dividends, filing status, deductions, and NIIT inputs under **Set up tax estimate**. Include realized gains from this ledger and other accounts in your income inputs; they are not automatically added. Federal/NC standard deductions initialize from filing status but can be overridden. Blank MAGI uses total income entered above. No income or trade records are sent to the market-data API.

The calculation compares tax before/after the proposed sale: progressive ordinary rates for short-term gains, preferential capital-gain stacking for long-term gains, the lower ordinary-tax alternative, incremental 3.8% NIIT where applicable, and NC's 3.99% flat rate after the entered deduction. Holding periods use calendar anniversaries; long-term requires more than one year. Tax year support is intentionally limited to 2026; other sale dates still show gains but not tax amounts. Demo prices are **never prefilled or used for position P&L**; enter a manual hypothetical price when a usable USD quote is unavailable.

This is a planning estimate, not tax-return preparation. Loss scenarios or histories with realized losses withhold the tax estimate because wash-sale basis adjustments and carryforwards need review. No automatic corporate actions/splits, transfers, dividends, specific-lot elections, wash-sale adjustments, annual loss netting, AMT, credits, income-driven deduction phaseouts, or NC income modifications. Enter complete, already split-adjusted records; verify cost basis and eligibility with your broker or a tax professional. Non-USD quote currencies are not supported for trading records or scenarios.

Tax references: [IRS 2026 rates and deductions](https://www.irs.gov/irb/2025-45_IRB), [IRS basis and holding periods](https://www.irs.gov/publications/p550), [NIIT](https://www.irs.gov/individuals/net-investment-income-tax), [NC rates](https://www.ncdor.gov/taxes-forms/individual-income-tax/tax-rate-schedules), and [NC deductions](https://www.ncdor.gov/taxes-forms/individual-income-tax/filing-topics/north-carolina-standard-deduction-or-north-carolina-itemized-deductions). Tables reviewed September 30, 2026.

Older workspaces acquire empty trade/tax settings without replacing existing data. Analyst widgets are added once beneath existing stock layouts (unless already present or the 30-widget limit is reached); intentionally removed widgets stay removed after migration. Add the market proxy to the home page through **Add widget → Analyst trend**; its original five-section layout remains intact.

## Free data and limitations

The default `auto` mode attempts these public sources server-side:

| Information                        | Source                                      | Behavior                                                                                      |
| ---------------------------------- | ------------------------------------------- | --------------------------------------------------------------------------------------------- |
| Quotes, charts, trading statistics | Yahoo Finance public chart endpoint         | Cached for five minutes; displays the quote's market timestamp                                |
| Company headlines                  | Yahoo Finance RSS                           | Links to original stories; cached for five minutes                                            |
| Advanced metrics                   | Yahoo Finance public quote-summary endpoint | Free public session; no account/API key; missing fields remain unavailable                    |
| Analyst consensus                  | Yahoo Finance recommendation-trend module   | Five-level distribution; partial coverage reported; no fabricated coverage for ETFs           |
| Market / sector news               | Yahoo Finance and CNBC market RSS           | Aggregated, deduplicated, newest first; available sources remain usable if another feed fails |
| Earnings calendar                  | Nasdaq public earnings calendar             | Select a calendar day; dates and estimates can change                                         |
| Demo fallback                      | Bundled deterministic fixtures              | Explicit **DEMO** labels; historical illustrative prices and sample headlines/events          |

There are no paid API dependencies. Public endpoints are unofficial/best-effort and can throttle or change. [yfinance's own documentation](https://github.com/ranaroussi/yfinance) describes Yahoo data as intended for personal use; Folio uses the HTTP endpoints directly rather than the Python library. Provider access is not a redistribution license. This MVP is intended for personal research, not a hosted market-data service.

The server applies a seven-second timeout per upstream request, bounded process-local caching, request coalescing, response validation, and clearly labeled demo fallbacks. The first fundamentals request includes Yahoo's public cookie/crumb session handshake, held only on the server. Fallbacks are cached for five minutes, then retried. A source badge's tooltip includes its source, timestamp, and fallback explanation. **DELAYED** means public data, not a guarantee of real-time delivery. **DEMO** prices, metrics, chart history, news, and earnings are illustrative and must not be treated as current financial information. Unknown demo symbols return an unavailable state instead of fabricated prices. Demo ETF company fundamentals remain unavailable.

Analyst labels map strong sell/sell/hold/buy/strong buy to very bearish/bearish/neutral/bullish/very bullish. Consensus is the recommendation-count-weighted mean on a 1–5 scale, rounded to the nearest label; it is not a proprietary forecast. A watchlist proxy is not the full market or a capitalization-weighted index. Prior-month comparisons use only stocks covered in both periods. Auto mode excludes demo fallbacks from consensus entirely; explicit UI demo mode displays illustrative recommendations with a prominent label. ETFs and uncovered stocks display no coverage rather than neutral.

An earnings date picker controls the day being viewed; there is no paid earnings-consensus database. Forward valuation metrics are provider forecasts, not reported results. RSS news coverage is a finite collection from the available feeds, not an exhaustive market-news archive. Analyst price targets, order execution, full-exchange screeners, and alerts are outside this MVP.

## Architecture

```text
src/app/                         Next.js app shell and market API
src/components/                  Canvas, widget library, settings, and widget views
src/lib/workspace.ts             Versioned schemas, defaults, and layout operations
src/lib/layout.ts                Minimal-displacement placement and swaps
src/lib/use-workspace.ts         Local persistence and cross-tab synchronization
src/lib/market/types.ts          Provider and normalized data contracts
src/lib/market/providers.ts      Yahoo quotes/RSS and Nasdaq earnings adapters
src/lib/market/fundamentals.ts    Public Yahoo session and normalized fundamentals
src/lib/market/analyst.ts         Recommendation parsing and consensus scoring
src/lib/portfolio.ts             Decimal FIFO ledger and sale simulation
src/lib/tax.ts                   2026 federal / North Carolina planning estimate
src/lib/market/market-news.ts     Broad news aggregation and duplicate removal
src/lib/market/research.ts        Metric definitions and headline sector tagging
src/lib/market/service.ts        Cache, request coalescing, demo policy
src/lib/market/demo.ts           Deterministic illustrative data
tests/                          Unit and API tests (upstreams mocked)
e2e/                            Browser tests against the production build
```

React/TypeScript, Next.js App Router, react-grid-layout, SWR, and Zod form the core. Native dialogs provide focus trapping and Escape dismissal. No external database or analytics are used.

To add a widget, extend `widgetTypes`, the catalog/default configuration in `workspace.ts`, then add its view and configurable fields in the components. All widgets reuse the same page layout and persistence layer. To replace quote data, implement `MarketProvider` and select the adapter in `service.ts`; views consume normalized envelopes and do not depend on upstream payload shapes. News and earnings adapters have separate normalized contracts.

Workspace files are versioned. Unsupported versions and invalid references are rejected, preserving the existing workspace. The parser caps imports at 2 MB. Browser storage quotas remain browser-dependent.

## Verification

```sh
npm run lint
npm run typecheck
npm test
npm run build
npx playwright install chromium
npm run test:e2e
```

Alternatively, use an installed Chrome browser:

```sh
PLAYWRIGHT_CHANNEL=chrome npm run test:e2e
```

`npm run check` runs lint, type checking, unit/API tests, the production build, and browser tests in order. Install a Playwright browser first, or supply `PLAYWRIGHT_CHANNEL=chrome`. Browser tests start an isolated production server on port 3100 with forced demo data; they don't depend on live providers. Failure traces and screenshots are written to `test-results/`.

Tests cover schema validation and upgrades, daily-return math across chart ranges, fundamentals normalization, public-session handling, upstream parsing, request coalescing, fallback labeling, unsafe feed links, news deduplication and sector tagging, drag/resize persistence and bounded collision resolution, metric toggles, independent sector layouts, custom dashboards, per-stock layouts and notes, watchlists, import/export, mobile layouts, and error recovery. Live-feed availability is checked separately, not asserted as a stable test condition.

Portfolio tests cover FIFO fee allocation, fractional shares, full/partial disposals, chronological ordering, invalid history/import rejection, holding-period/leap-year boundaries, bracket crossings, gain stacking, the ordinary-tax cap, NIIT, NC tax, loss handling, persistent edits, manual sale prices, and mobile trade dialogs. Analyst tests cover parsing, all five labels, missing coverage, public-provider requests, fallback exclusion, and watchlist selection.

## Git and deployment

The initial MVP is on `feat/modular-workspace`; metrics, sector news, and layout improvements are on `feat/metrics-news-layout`; trade ledgers, tax estimates, and analyst consensus are on `feat/trades-tax-analyst`, with conventional commits. The included GitHub Actions workflow verifies pushes and pull requests. Push the branch and open a PR when ready; this workspace does not automatically publish or deploy your work.

Local use is free. Run `npm run build && npm start` on a machine you control, or deploy the Next.js app to a host with Node.js support. Hosting providers may have their own usage limits or charges. For remote personal access, put the app behind your own access controls; the MVP has no authentication or public-service rate limiting. A static file host alone cannot run the market API. Each server instance has its own ephemeral cache; user work remains in the browser, not on the host.
