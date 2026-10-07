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
