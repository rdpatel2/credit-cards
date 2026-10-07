# Card Compass implementation

Native desktop application for US credit cards, supporting Windows, macOS, and Linux.

Work proceeds on stacked branches; each step builds on the preceding commit.

1. `feat/catalog`: Python package, live source adapter, validated local cache, profile storage, offline snapshot, data tests.
2. `feat/recommendations`: wallet coverage, spend-based incremental reward estimates, annual fees, point values, offer affordability, calculation tests.
3. `feat/desktop`: Qt desktop interface, wallet editor, spending and coverage inputs, card search, recommendations, benefit and offer details, asynchronous startup refresh.
4. `chore/release`: desktop smoke tests, cross-platform launch/build instructions, final verification and integration into `main`.

## Product decisions

- User information stays on the device. No bank login or card numbers.
- Fetch published source data on every startup in a background worker. Keep the last successful dataset when offline.
- Fetch time and source review time are distinct and visible. Refreshing a community feed does not verify each issuer's current offer.
- Compare a new card to the best rewards already available in the wallet. Show recurring value separately from first-year value.
- Incomplete, activation-dependent, rotating, and selected categories need explicit treatment; never assume every menu category earns at once.
- Benefits are shown with their terms, not automatically converted into cash value.
- Use native Qt widgets, with no web server or browser-based UI.

## Validation

Run data and recommendation tests, then an offscreen Qt workflow covering spending edits, wallet changes, filtering, profile persistence, and refresh error recovery. Live-download validation is separate from offline deterministic tests. Build distributable executables on each target OS using PyInstaller.

## Completed validation

- Live source download: 188 cards, with an offline snapshot included.
- 23 tests covering cache preservation and cancellation, reward allocations, shared caps, offer filtering, and native GUI workflows.
- Linux desktop executable built with PyInstaller and exercised with `--smoke-test` using the packaged snapshot and solver.
- Windows and macOS build/test jobs configured in GitHub Actions; those platforms require execution of the jobs to confirm runtime compatibility.

## Desktop redesign · `feat/gui-redesign`

Replace the dense three-column screen with a sidebar and dedicated task pages. Give matches a summary dashboard, a featured recommendation, and readable rows with scalable card identities. Move global assumptions to Preferences and separate card details into Overview, Offers, Benefits, and Sources tabs. Preserve saved profiles and the existing recommendation engine.

Validate navigation, automatic saving, reopening detail windows with the keyboard, issuer filtering, and the layout at 1040 × 720. Rebuild the Linux distributable so the packaged app includes the redesign; Windows/macOS checks remain in the configured platform workflow.

Redesign validation: 27 tests pass, the layout was inspected at 1380 × 900 and 1040 × 720, and the rebuilt Linux executable passes the packaged smoke test. `docs/gui-preview.png` shows sample data in the redesigned workspace.

## Statement imports · `feat/statement-imports`

1. PDF parsing and storage: BOFA and Amex selectable-text extraction, integer-cent transactions, date and total checks, local SQLite ledger, duplicate detection, merchant aliases, and global/issuer/card category rules.
2. Native desktop workflow: Statements page, background PDF reading, first-time merchant categorization, purchase review and individual overrides, saved-merchant editing, and optional imported-spending preview.
3. Validation and documentation: closing-date and wallet-card guards, safe cancellation during extraction, account reassignment, import removal, parser edge cases, workflow tests, usage instructions, and packaged PDF smoke coverage.

Validation: 58 tests pass, including generated BOFA/Amex PDFs, merchant memory, duplicate occurrences, refunds, rule scopes, historical edits, atomic rollback, and native UI import/cancellation workflows. The Linux executable's smoke test exercises PDF parsing, SQLite persistence, remembered categories, and duplicate detection alongside the catalog and solver. Windows/macOS runtime checks remain pending the configured platform jobs.

No original PDFs are retained or transmitted. Initial layout support is based on the supplied statement text and synthetic fixtures; scanned PDFs/OCR and CSV imports are outside this feature.

## Readability corrections · `fix/ui-contrast`

Darken secondary text, table headings, issuer labels, disabled controls, and coverage gap values. Brighten sidebar captions and make selected tabs more distinct. Apply an explicit light Qt palette and dropdown/date-field colors so native controls remain consistent when the desktop uses a dark theme.

Validation: all 58 tests pass. The dashboard and statement review were visually inspected offscreen, and dropdown/date-field colors were checked with a simulated dark desktop palette. Sampled secondary, disabled, sidebar, and gap text pairs exceed a 4.5:1 contrast ratio. The rebuilt Linux executable passes its desktop and statement smoke checks.

## Category search and deliberate input · `feat/searchable-categories`

1. Shared controls: searchable category dropdowns with case-insensitive substring suggestions, valid-selection checks, and scroll-safe dropdown/numeric/date controls. Search also filters the category lists in Card settings without removing saved selections.
2. Validation and documentation: keyboard completion, invalid text rejection, unfinished purchase override guards, focused/unfocused wheel checks, and preserved keyboard changes. All 63 tests pass. The rebuilt Linux executable passes its desktop and statement smoke checks.

## Catalog discovery and source review · `fix/catalog-source-verification`

1. Search and provenance: normalize names and word order, support common issuer aliases, avoid searching reference URLs as product features, label source domains, record the dated BOFA issuer check, and correct its unnecessary standing-category activation gate.
2. Validation and audit: refresh into a temporary directory, audit all 188 cards and 445 unique source links, record HTTP results without treating them as term verification, and test discovery, source display, provenance, and reward selection. All 69 tests pass. The rebuilt Linux package passes its desktop and PDF smoke checks.

The source review and full machine-readable report are in `docs/source-review.md` and `docs/source-audit.json`. Only the specified BOFA terms were independently checked against issuer pages; the broad catalog audit checks provenance and link availability. The BOFA introductory earning boost remains excluded from numeric estimates and is explicitly shown as a limitation.

## CSV and PDF statements · `feat/csv-statement-imports`

1. CSV parser: suggested and custom column mappings, explicit amount conventions, US/ISO dates, quoted values and common delimiters, optional account/reference/category/type data, exact cents, and invalid-row rejection.
2. Desktop workflow: one CSV/PDF picker, a scrollable CSV mapping dialog and sample rows, background file reading, issuer association from the selected wallet card, and reuse of merchant review, category memory, duplicate checks, and spending previews.
3. Validation and packaging: positive/negative purchases, debit/credit exports, payments/refunds/fees, invalid rows, account isolation, CSV re-imports, native mapping/review tests, and packaged CSV persistence smoke coverage. All 87 tests pass. The rebuilt Linux executable passes catalog, PDF, and CSV smoke checks.

CSV imports support mapped exports from any wallet issuer; PDF layouts remain BOFA/Amex selectable text. One file represents one statement period for spending averages. Cross-format duplicate detection requires matching transaction/account fields and cannot be guaranteed when exports differ. Original files are not retained or transmitted.

## Rewards-category persistence · `fix/category-selection-saving`

1. Replace single-choice rewards checklists with searchable dropdowns: highlighting an old checklist row previously saved no category unless its checkbox was also checked. Require a valid choice before saving, restore persisted choices, hide BOFA's unnecessary activation checkbox, and map source `online_shopping` to the existing `online_retail` spending category. Shared category pickers notify consumers when text changes or the same item is selected again.
2. Regression validation: exercise real mouse selection and Save, verify Dining and Online Shopping coverage, reopen the application and settings to confirm persistence, reject an empty rewards choice, and check same-category reselection. All 91 tests pass. The rebuilt Linux executable passes its catalog, PDF, and CSV smoke checks.

Existing profiles with an empty BOFA category list need an explicit selection after restarting; the app does not guess which issuer category an empty selection means. Select the current issuer category and Save to update Coverage. Shared caps still affect effective coverage rates.

## Imported spending breakdown · `feat/spending-breakdown`

1. Aggregation: integer-cent purchase/refund totals and rankings by category, remembered merchant, card, and transaction month. Resolve merchant aliases while preserving stored transaction categories; exclude payments and other non-spending entries, retain uncategorized purchases and negative refund totals.
2. Native desktop page: month/card filters, summary metrics, category/merchant bar charts, detailed tables, monthly trends, empty states, the local database path, and refresh after ledger changes. Scroll the page and sidebar at smaller window sizes so added navigation does not increase the minimum window dimensions.
3. Validation and documentation: all 99 tests pass, including filters, refund-only data, merchant aliases, preserved overrides, category edits, import removal, and the 1040 × 720 window. The layout was inspected at 1380 × 900 and 1040 × 720. The rebuilt Linux executable passes the new Breakdown smoke check alongside existing catalog/PDF/CSV checks.

`docs/breakdown-preview.png` uses synthetic transactions. Breakdown shows actual imported totals, rather than recommendation spending averages. Original statements are not retained or transmitted.

## Compound signup offers · `fix/compound-signup-offers`

1. Offer model and issuer refresh: preserve multiple reward components, validate amounts, value cash and miles without counting restricted credits, and check Venture Rewards directly during each catalog refresh. Retain dated terms marked unconfirmed if the issuer page is unavailable or unsupported; remove ended credits and promotion labels after a successful check.
2. Native offer display: show the full reward combination, readable limited-time badge, component restrictions, additional source perks, excluded-value notes, and issuer check provenance. Update the bundled Venture record against the live issuer page and extend the packaged offer smoke check.
3. Verification and documentation: 114 tests pass, covering compound parsing, changed/ended promotions, unsupported wording, cached fallback, catalog persistence, valuation, and native details. Live feed/page comparison confirms the missing $300 stays credit. The rebuilt Linux package passes desktop, offer, PDF, CSV, and spending-breakdown smoke checks. Document the Venture-only issuer adapter and credit valuation limits.

## Card-category dashboard · `feat/card-dashboard`

1. Discovery model: overlapping Cash back, Points, Miles, Travel, Dining, Groceries, No annual fee, and Business groups from published card data. Exclude discontinued and unknown-status cards from discovery while retaining saved wallet history. Travel requires earning categories, miles, or relevant travel benefits, rather than generic insurance alone.
2. Native dashboard: category tiles with active counts, persistent global search and issuer filter, optional full active-card list, results group labels, and a return-to-dashboard action. Startup opens Dashboard; search clears the reward-group filter; refresh updates counts and results. Sidebar availability reflects active catalog cards.
3. Validation and packaging: all 121 tests pass, including startup, tile membership/counts, global search after selecting a tile, discontinued search exclusion, wallet preservation, and refresh changes. Inspect layouts at 1380 × 900 and 1040 × 720; the dashboard scrolls at smaller sizes. The rebuilt Linux package passes dashboard, desktop, offer, PDF, CSV, and spending-breakdown smoke checks. `docs/dashboard-preview.png` shows an empty local profile.
