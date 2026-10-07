# Card Compass

A native desktop app for choosing your next US credit card. Runs on Windows, macOS, and Linux using Python and Qt. There is no web server.

[Preview the desktop workspace](docs/gui-preview.png) (sample wallet and spending).

- Search cards, benefits, earning categories, annual fees, and published signup offers.
- Add your existing cards to a saved wallet, including manually entered cards.
- Enter monthly spending; Coverage puts your highest spending categories first.
- Import CSV statement exports or BOFA/Amex PDFs, categorize each new merchant once, and reuse your saved choices on later imports.
- See your biggest spending categories and merchants in **Breakdown**, with month/card filters, refunds, and monthly trends.
- Infer coverage from your wallet, or override a category's cash-equivalent rate. Set **0%** for an uncovered category and **Auto** to derive it from cards.
- Compare **additional** annual rewards against your existing wallet, after the new card's fee. Recurring and first-year estimates are separate.
- Refresh the public catalog in the background on every startup. Offline starts use the last successful cache, then the bundled snapshot.

## Run

Install Python 3.10 or newer. From this directory:

```sh
python -m venv .venv
```

Activate the environment:

```sh
# Windows PowerShell
.venv\Scripts\Activate.ps1

# macOS / Linux
source .venv/bin/activate
```

Then:

```sh
python -m pip install -e .
python -m card_compass
```

The installed `card-compass` command also launches the app. Linux requires a desktop session and Qt system libraries; on Ubuntu install `libegl1 libopengl0 libxcb-cursor0 libxkbcommon-x11-0` if Qt reports missing platform libraries. If you prefer not to activate a virtual environment, use `.venv/bin/python` on macOS/Linux or `.venv\Scripts\python.exe` on Windows directly.

## Use

1. In **Explore cards**, search for each card you own, select it, and click **Add to wallet**. Use **My wallet → Add manually** for cards missing from the catalog.
2. In **Spending**, enter monthly purchases in separate buckets. **Add a category** includes merchant and travel-portal categories. Move that spending out of other buckets to avoid double counting.
3. Use **Coverage** to see gaps against your adjustable target, initially 2%. Manual coverage entries replace inferred wallet coverage for that category.
4. Compare **Your matches**, which highlights your strongest match and its incremental value. Choose ongoing or first-year sorting, and use **Preferences** to set your maximum annual fee, point values, coverage target, and bonus assumptions.
5. Adjust point values, selections, and activation in **Card settings**. The default assumption is **1 cent per point/mile across all programs**; change it to your actual redemption values, especially for hotel points.
6. Signup offers are displayed automatically. Enable **Count public signup bonuses** only if you expect to qualify. Only public, unexpired offers with spend requirements your stated spending pace can meet enter the first-year estimate. Alternative offers are compared individually; they are not added together.

The desktop workspace uses dedicated screens for matches, wallet, spending, coverage, catalog browsing, and preferences. Card details open in a separate window with Overview, Offers, Benefits, and Sources tabs. Click a card or press Enter to open its details; Escape closes the detail window. The interface supports a minimum window size of 1040 × 720, with scrollable settings and spending screens. Card illustrations and navigation icons are drawn as scalable vectors; illustrations are identifiers rather than official issuer artwork.

For cards with one selectable rewards category, **Card settings** uses a searchable dropdown. Choose the category currently set with your issuer and click **Save**; Coverage updates using that selection and restores it when you reopen the app. Multi-category benefits retain checkbox lists. For Bank of America Customized Cash Rewards, choose **Dining** to apply its standing 3% dining rate; separate activation is not required. Its **Online Shopping** option maps to the **Online retail** spending bucket. Coverage displays effective rewards after any shared spending cap, so large spending amounts can show less than the advertised category rate.

## Import statements

1. Add your cards to **My wallet**, then open **Statements → Import CSV or PDF**.
2. For CSVs, confirm the suggested column mappings and select the amount convention: purchases positive, purchases negative, or separate debit/credit columns. Date, description, and amount columns are required; reference, posting date, account, category, and type columns are optional. You can supply the account’s last 4–5 digits if the export omits it.
3. Select the matching wallet card and verify the statement closing date. If the date needs correction, click **Read dates again** before reviewing. CSVs initially infer it from the latest posting date.
4. In **New merchants**, choose a category for each unfamiliar merchant. Repeated purchases are grouped together. You can link a description to an existing merchant and remember the category across all cards, for this issuer, or for this card only.
5. Review the purchases, exclusions, and parsing warnings. A purchase's category can be overridden without changing the merchant's default. Confirm the review and save.
6. Optionally choose **Use imported spending** to preview and replace your monthly spending inputs. Saving a statement alone does not replace them.

Later imports reuse saved merchant choices. Re-imported transactions are skipped by default; explicitly include a flagged duplicate only if it represents another purchase. Identical purchases within one statement retain their separate occurrences. Payments, interest, fees, and reward credits are excluded from spending; merchant refunds reduce their category totals.

Type part of a category name in a category picker to search, then choose a matching suggestion with the keyboard or mouse. Search ignores capitalization and matches words anywhere in the label. This works for statement categories and **Add a category**; Card settings has search fields for its category lists. Unfinished category searches cannot be saved as purchase overrides. Scrolling over dropdowns, dates, and numeric fields does not change their values; use keyboard input or an explicit dropdown selection.

The **Saved merchants** tab lets you change defaults and optionally apply them to past transactions; individual overrides are preserved. **Accounts** lets you correct a linked wallet card. **Imported files** lets you remove an import while retaining merchant choices, then re-import it if needed. Reapply imported spending after changing or removing transactions to update the recommendation inputs.

Monthly spending is calculated separately for each card: category totals divided by the number of distinct imported statement closing months, then added across cards. This is an average of imported statement periods, so import complete statements for the months you want represented. The preview replaces all manual spending buckets after confirmation; coverage settings remain separate.

The initial parsers support selectable-text BOFA and Amex layouts based on the supplied examples. Tests use synthetic PDFs; other statement layouts may need adjustments. Scanned PDFs require OCR, which is not included, and password-protected PDFs must be unlocked first. BOFA purchase totals are checked when available. Printed Amex merchant categories are displayed as hints; your choices determine the spending categories. Reference numbers do not determine categories, and these categories do not verify issuer bonus eligibility.

CSV imports work with any wallet card when its export can be mapped to the required columns. Comma, semicolon, and tab delimiters and quoted values are supported; dates use US `MM/DD/YYYY`, `MM/DD/YY`, or ISO `YYYY-MM-DD`, with US dollar amounts. Export one billing period per file for spending averages. Invalid transaction rows stop the import with a row number rather than silently dropping purchases. CSVs have no statement total to reconcile. XLS/XLSX workbooks are not supported; export them as CSV first.

Re-importing the same CSV reuses merchant rules and duplicate checks. PDF and CSV exports may use different descriptions, posting dates, references, or account endings, so duplicate detection across formats is not guaranteed. Review overlapping imports before saving, and remove an earlier import before replacing it with corrected column/sign settings. CSVs without account endings use the selected wallet card as the account identity.

PDFs and CSVs are read locally and are not retained by the app. Extracted transactions, account endings, import metadata, and merchant choices are saved locally in `transactions.sqlite3`.

## Spending breakdown

Open **Breakdown** in the sidebar after importing statements. It shows purchases, refunds, and net spending, with charts for the largest categories and merchants. Tabs list all categories, merchants, cards, and calendar months. Choose a month or card to narrow the results.

Rankings use net spending (purchases minus refunds). The Share column shows the portion of purchases before refunds, so refunds do not create misleading percentages. Payments, fees, interest, transfers, and reward credits are excluded. Uncategorized purchases remain included. Saved merchant aliases group repeat descriptions; individual transaction category overrides remain respected.

These are actual totals from imported transactions, grouped by transaction date, rather than the statement-period averages used for recommendations. Missing months are not estimated and manual spending inputs are not included. The page updates after imports, category/history edits, account reassignment, or import removal. The database location also appears at the bottom of the page.

[Preview the Breakdown page](docs/breakdown-preview.png) (synthetic sample purchases).

## Data and calculation limits

Catalog data comes from [fuermosi777/rewards](https://github.com/fuermosi777/rewards), a public community-maintained database with issuer source links and review dates. Each startup refresh downloads card and bonus JSON and checks Venture Rewards' signup offer directly against Capital One's public page. Other cards remain community-sourced; a fresh download may contain old reviews. Check issuer links before applying. The bundled snapshot's download timestamp is stored in `card_compass/data/snapshot.json`.

Compound offers display each reward, its restrictions, and any additional perks. Offers explicitly described as limited-time receive a **Limited-time offer** badge; the app does not invent a promotion end date. The Venture adapter reads the miles, spending requirement, and optional one-time Capital One Travel Stays credit as one offer. A successful issuer check replaces old public terms, including removing an ended credit or limited-time label. If the page is blocked or its wording is unrecognized, the app retains dated last-available terms and marks the current issuer offer **unconfirmed**. This check runs during refresh, not when opening a card's details. Months are approximated as 30 days for spending feasibility.

Catalog search accepts issuer names and common aliases such as **BOFA**, **Amex**, and **US Bank**, with words in any order. For example, search **Bank of America customized cash** to find Customized Cash Rewards. The Sources tab labels issuer-domain links, third-party references, and other domains that need review. Those labels identify provenance, not verified current terms. Independently checked terms appear with their check date; Bank of America Customized Cash Rewards has a review dated October 7, 2026. Its first-year 6% category promotion is displayed but excluded from numeric estimates. See the [catalog source audit](docs/source-review.md) for the scope, results, and remaining limits.

The allocation solver maximizes wallet rewards subject to category spending and shared caps. Estimates assume steady monthly spending and annualize monthly/quarterly caps; existing cap usage and changes during the year are not modeled. Category aliases cover a small set of synonymous labels; issuer eligibility and exclusions still apply. Restricted merchant and portal categories remain distinct. Broad travel categories are matched exactly and do not automatically include separate airfare/hotel buckets.

Selection-dependent bonuses are excluded until configured. Automatic highest-category bonuses assume your selected categories will be the highest on that particular card each cycle. Rotating bonuses appear in details but are excluded from annual estimates; an old quarter is marked unresolved. The adapter handles cross-record shared caps for Bank of America Customized Cash and Citi Costco, and corrects AAA Daily Advantage's reward-denominated cap from its source terms. Other caps follow their source records; incomplete data cannot guarantee an exhaustive optimization.

Signup spending feasibility uses total monthly spending × days / 30. It assumes that spending can be charged to the new card; excluded purchases, issuer eligibility, and the reward opportunity cost of rerouting spending to earn a bonus are not modeled. First-year figures use the annual fee waiver in the source and count one qualifying public offer if enabled. Cash and points/miles components within that offer are combined; alternative offers are never added together. Statement and travel credits, memberships, free nights, interest, taxes, and approval odds are excluded from numeric rankings. The Offers tab identifies excluded components separately. Benefits and their terms are listed for your own evaluation. Existing wallet annual fees are common to both scenarios and cancel out of the comparison.

Your wallet and spending stay on your device. Refresh requests send no profile information; opening an issuer source link launches your browser. Local files:

| OS | Location |
| --- | --- |
| Windows | `%LOCALAPPDATA%\CardCompass` |
| macOS | `~/Library/Application Support/CardCompass` |
| Linux | `$XDG_DATA_HOME/CardCompass` or `~/.local/share/CardCompass` |

`profile.json` holds preferences, wallet and spending; `catalog.json` holds the last complete download; `transactions.sqlite3` holds imported statement data and merchant choices. Failed refreshes preserve the old catalog. A close during refresh cancels pending requests and waits for in-flight requests to finish (up to their network timeout). Custom cards are preserved across catalog refreshes.

## Test and build

```sh
python -m pip install -e ".[dev]"
python -m pytest -q
python -m PyInstaller --noconfirm --windowed --onedir --name CardCompass --collect-data card_compass run_app.py
```

To exercise a packaged app without live requests or writing a real profile, run it with `--smoke-test`. On Linux a headless check is `QT_QPA_PLATFORM=offscreen dist/CardCompass/CardCompass --smoke-test`.

Distributables are in `dist/`. Build on each target operating system: PyInstaller does not cross-compile. Windows produces an `.exe`, macOS an `.app`, and Linux a native executable directory. Keep all files in the generated directory together. These are unsigned development builds; signing/notarization is a separate distribution step.

`.github/workflows/desktop.yml` runs tests and creates downloadable build artifacts for all three platforms when pushed to GitHub or triggered manually. Local validation covers Linux offscreen UI workflows; Windows/macOS validation remains pending until those jobs run.

## Implementation history

See [IMPLEMENTATION.md](IMPLEMENTATION.md). The branches form a stack: `feat/catalog` → `feat/recommendations` → `feat/desktop` → `chore/release`. Each branch retains its completed milestone. `main` is advanced to the fully verified result after the release step. No remote push is required to run locally.

The desktop redesign is on `feat/gui-redesign`, with separate commits for the visual components, task-based workspace and workflow tests, and documentation/preview. The completed redesign is integrated into `main`.

Statement imports are on `feat/statement-imports`, with separate commits for PDF parsing and storage, native review and merchant mapping, and validation/documentation. The completed feature is integrated into `main`.
